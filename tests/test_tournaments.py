import csv
import multiprocessing
import os
import random
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from ranking import standings
from storage import Store, StoreError
from tournaments import tournament_state


def ready(state):
    return [f for f in state['fixtures'] if not f['bye'] and f['result'] is None]


def record(tournament, fixture, first_wins=True):
    return dict(id=f"result-{fixture['id']}", timestamp='2026-09-17T00:00:00+00:00',
                tournament_id=tournament['id'], fixture_id=fixture['id'],
                player1=fixture['player1'], player2=fixture['player2'],
                score1=11 if first_wins else 5, score2=5 if first_wins else 11)


def play(tournament, random_seed=0):
    rng = random.Random(random_seed)
    matches = []
    while True:
        state = tournament_state(tournament, matches)
        if state['complete']:
            return state, matches
        fixtures = ready(state)
        if not fixtures or len(matches) > 1000:
            raise AssertionError('Tournament is stuck')
        fixture = rng.choice(fixtures)
        matches.append(record(tournament, fixture, bool(rng.randrange(2))))


def concurrent_result(directory, tournament_id, fixture, queue):
    store = Store(directory)
    for _ in range(100):
        try:
            store.register_tournament_match(tournament_id, fixture['id'], 11, 5,
                                             (fixture['player1'], fixture['player2']))
            queue.put('saved')
            return
        except StoreError as exc:
            if 'busy' in str(exc):
                time.sleep(0.01)
            else:
                queue.put(str(exc))
                return
    queue.put('lock timeout')


class ScheduleTests(unittest.TestCase):
    def tournament(self, system, count, groups=1):
        return dict(id='t', name='Cup', system=system, players=[str(i) for i in range(count)], groups=groups)

    def test_round_robin_every_pair_once_even_and_odd(self):
        for count in range(2, 12):
            with self.subTest(count=count):
                tournament = self.tournament('round_robin', count)
                state, matches = play(tournament)
                self.assertEqual(len(matches), count * (count - 1) // 2)
                self.assertEqual(len({frozenset((m['player1'], m['player2'])) for m in matches}), len(matches))
                self.assertEqual(state['champion'], state['tables'][0]['rows'][0]['player'])
                self.assertEqual(Counter(p for m in matches for p in (m['player1'], m['player2'])),
                                 Counter({str(i): count - 1 for i in range(count)}))

    def test_single_elimination_even_non_power_of_two(self):
        for count in range(2, 22, 2):
            for seed in range(3):
                with self.subTest(count=count, seed=seed):
                    tournament = self.tournament('single', count)
                    state, matches = play(tournament, seed)
                    self.assertEqual(len(matches), count - 1)
                    losses = Counter(m['player2'] if m['score1'] > m['score2'] else m['player1'] for m in matches)
                    self.assertNotIn(state['champion'], losses)
                    self.assertEqual(set(losses.values()), {1})

    def test_group_double_elimination_loss_routing_and_one_final(self):
        for count in range(4, 18):
            for groups in range(1, count // 2 + 1):
                with self.subTest(count=count, groups=groups):
                    tournament = self.tournament('group_double', count, groups)
                    rng = random.Random(count * groups)
                    matches, losses = [], None
                    while True:
                        state = tournament_state(tournament, matches)
                        if state['phase'] != 'Group stage' and losses is None:
                            losses = {}
                            for table in state['tables']:
                                for i, row in enumerate(table['rows']):
                                    losses[row['player']] = int(i >= (len(table['rows']) + 1) // 2)
                        if state['complete']:
                            break
                        self.assertTrue(ready(state))
                        fixture = rng.choice(ready(state))
                        if fixture['stage'] == 'Upper bracket':
                            self.assertEqual((losses[fixture['player1']], losses[fixture['player2']]), (0, 0))
                        elif fixture['stage'] == 'Lower bracket':
                            self.assertEqual((losses[fixture['player1']], losses[fixture['player2']]), (1, 1))
                        elif fixture['stage'] == 'Grand final':
                            self.assertEqual((losses[fixture['player1']], losses[fixture['player2']]), (0, 1))
                        result = record(tournament, fixture, bool(rng.randrange(2)))
                        matches.append(result)
                        if losses is not None:
                            loser = result['player2'] if result['score1'] > result['score2'] else result['player1']
                            losses[loser] += 1
                    finals = [f for f in state['fixtures'] if f['stage'] == 'Grand final']
                    self.assertEqual(len(finals), 1)
                    self.assertEqual(state['champion'], finals[0]['winner'])
                    self.assertFalse(any(f['id'] == 'reset' for f in state['fixtures']))
                    if count % 2:
                        rows = [row for table in state['tables'] for row in table['rows']]
                        self.assertEqual({row['player'] for row in rows}, set(tournament['players']))
                        best = min(rows, key=lambda row: (-row['wins'],
                                   -(row['scored'] - row['conceded']), -row['scored'],
                                   tournament['players'].index(row['player'])))['player']
                        bye = next(f for f in state['fixtures'] if f['id'] == 'u1-1')
                        self.assertTrue(bye['bye'])
                        self.assertEqual(bye['winner'], best)

    def test_odd_group_bye_uses_group_performance_not_draw_seed(self):
        tournament = self.tournament('group_double', 5, 2)
        initial = tournament_state(tournament, [])
        # The last entrant beats both group opponents; the first seed loses.
        matches = [record(tournament, f, f['player1'] == '4' or
                          (f['player2'] != '4' and f['player1'] != '0'))
                   for f in initial['fixtures']]
        state = tournament_state(tournament, matches)
        bye = next(f for f in state['fixtures'] if f['id'] == 'u1-1')
        self.assertTrue(bye['bye'])
        self.assertEqual(bye['winner'], '4')

    def test_round_robin_tie_uses_saved_draw_order(self):
        tournament = self.tournament('round_robin', 3)
        initial = tournament_state(tournament, [])
        # Rock-paper-scissors: all players end with one win and equal point totals.
        winners = {frozenset(('0', '1')): '0', frozenset(('1', '2')): '1', frozenset(('0', '2')): '2'}
        matches = [record(tournament, f, winners[frozenset((f['player1'], f['player2']))] == f['player1'])
                   for f in initial['fixtures']]
        self.assertEqual(tournament_state(tournament, matches)['champion'], '0')


class TournamentStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('PING_PONG_TEST_ROOT'))
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        for name in ('Alice', 'Bob', 'Carol', 'Dan', 'Eve', 'Frank'):
            self.store.save_player(name)
        self.ids = [p['id'] for p in self.store.snapshot()[0]]

    def create(self, system='single', count=4, groups=1):
        return self.store.create_tournament('Test cup', system, self.ids[:count], groups)[2][-1]

    def save(self, tournament, fixture):
        return self.store.register_tournament_match(tournament['id'], fixture['id'], 11, 5,
                                                   (fixture['player1'], fixture['player2']))

    def test_existing_csv_upgrades_only_when_saving(self):
        path = Path(self.temp.name) / 'matches.csv'
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=Store.LEGACY_MATCH_FIELDS)
            writer.writeheader()
            writer.writerow(dict(id='old', timestamp='2026-09-17T00:00:00+00:00', player1=self.ids[0],
                                 player2=self.ids[1], score1=11, score2=8))
        before = path.read_bytes()
        self.assertEqual(self.store.snapshot()[1][0]['tournament_id'], '')
        self.assertEqual(path.read_bytes(), before)
        tournament = self.create()
        self.save(tournament, ready(tournament_state(tournament, []))[0])
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot[1][0]['id'], 'old')
        self.assertEqual(len(snapshot[1]), 2)
        self.assertEqual(sum(s['matches'] for s in standings(snapshot[0], snapshot[1])), 4)

    def test_creation_validation(self):
        for system, ids, groups in [('single', self.ids[:3], 1),
                                    ('group_double', self.ids[:2], 1), ('group_double', self.ids[:4], 3),
                                    ('single', self.ids[:1], 1), ('single', self.ids[:2] * 2, 1)]:
            with self.subTest(system=system, ids=ids, groups=groups), self.assertRaises(StoreError):
                self.store.create_tournament('Cup', system, ids, groups)
        self.create('round_robin', 3)
        self.create('group_double', 5, 2)
        self.store.set_active(self.ids[0], False)
        with self.assertRaises(StoreError):
            self.create()

    def test_completion_reopen_and_global_elo(self):
        tournament = self.create('group_double', 5, 2)
        while True:
            players, matches, _ = self.store.snapshot()
            state = tournament_state(tournament, matches)
            if state['complete']:
                break
            self.save(tournament, ready(state)[0])
        self.assertEqual(len(Store(self.temp.name).snapshot()[2]), 1)
        self.assertEqual(sum(s['matches'] for s in standings(players, matches)), 2 * len(matches))
        self.assertAlmostEqual(sum(s['elo'] for s in standings(players, matches)), 6000)
        self.assertEqual(tournament_state(tournament, Store(self.temp.name).snapshot()[1]), state)
        with self.assertRaisesRegex(StoreError, 'reverse'):
            self.store.delete_match(matches[0]['id'])
        self.store.delete_match(matches[-1]['id'])
        self.assertFalse(tournament_state(tournament, self.store.snapshot()[1])['complete'])

    def test_duplicate_stale_and_failed_result_do_not_change_ledger(self):
        tournament = self.create()
        fixture = ready(tournament_state(tournament, []))[0]
        with self.assertRaisesRegex(StoreError, 'participants changed'):
            self.store.register_tournament_match(tournament['id'], fixture['id'], 11, 5, ('old', 'ids'))
        with patch('storage.os.replace', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                self.save(tournament, fixture)
        self.assertEqual(self.store.snapshot()[1], [])
        self.save(tournament, fixture)
        with self.assertRaisesRegex(StoreError, 'already'):
            self.save(tournament, fixture)
        self.assertEqual(len(self.store.snapshot()[1]), 1)

    def test_registered_retired_player_can_finish_tournament(self):
        tournament = self.create('single', 2)
        self.store.set_active(self.ids[0], False)
        self.save(tournament, ready(tournament_state(tournament, []))[0])
        self.assertTrue(tournament_state(tournament, self.store.snapshot()[1])['complete'])

    def test_two_processes_cannot_record_same_fixture_twice(self):
        tournament = self.create()
        fixture = ready(tournament_state(tournament, []))[0]
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        processes = [ctx.Process(target=concurrent_result,
                                 args=(self.temp.name, tournament['id'], fixture, queue)) for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(process.exitcode, 0)
        results = [queue.get(timeout=2) for _ in processes]
        queue.close()
        self.assertEqual(results.count('saved'), 1)
        self.assertTrue(any('already' in result for result in results))
        self.assertEqual(len(self.store.snapshot()[1]), 1)
