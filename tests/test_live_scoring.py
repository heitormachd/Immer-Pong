import csv
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from live_scoring import live_state
from ranking import standings
from storage import Store, StoreError


def raw_match(sequence, target=7):
    return dict(player1='A', player2='B', target_points=target,
                point_log=[dict(player=p, timestamp='2026-09-17T00:00:00+00:00') for p in sequence])


def concurrent_point(directory, match_id, player, queue):
    for _ in range(100):
        try:
            Store(directory).score_live_match(match_id, player, 0)
            queue.put('saved')
            return
        except StoreError as exc:
            if 'busy' in str(exc):
                time.sleep(0.01)
            else:
                queue.put(str(exc))
                return
    queue.put('lock timeout')


class LiveRulesTests(unittest.TestCase):
    def test_normal_finish_and_no_points_after_winner(self):
        for target in (2, 7, 11):
            with self.subTest(target=target):
                state = live_state(raw_match('B' * (target - 2) + 'A' * target, target))
                self.assertEqual((state['winner'], state['score1'], state['score2']), ('A', target, target - 2))
                self.assertEqual(state['overtime_round'], 0)
        with self.assertRaisesRegex(ValueError, 'after'):
            live_state(raw_match('A' * 8))

    def test_deuce_first_point_does_not_win_and_split_rounds_reset(self):
        deuce = 'AB' * 6
        state = live_state(raw_match(deuce + 'A'))
        self.assertIsNone(state['winner'])
        self.assertEqual((state['score1'], state['score2'], state['overtime_score']), (7, 6, (1, 0)))
        # The middle BB crosses a reset boundary and must not win.
        state = live_state(raw_match(deuce + 'ABBA'))
        self.assertIsNone(state['winner'])
        self.assertEqual((state['overtime_round'], state['overtime_score']), (3, (0, 0)))
        self.assertEqual(sum(p['reset'] for p in state['history']), 2)
        state = live_state(raw_match(deuce + 'ABBA' + 'BB'))
        self.assertEqual((state['winner'], state['score1'], state['score2']), ('B', 8, 10))
        self.assertEqual(state['overtime_score'], (0, 2))

    def test_multiple_target_sizes_and_many_resets(self):
        for target in (2, 7, 11):
            sequence = 'AB' * (target - 1) + 'AB' * 30 + 'AA'
            state = live_state(raw_match(sequence, target))
            self.assertEqual(state['winner'], 'A')
            self.assertEqual(state['overtime_round'], 31)
            self.assertEqual(len(state['history']), len(sequence))


class LiveStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('PING_PONG_TEST_ROOT'))
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.save_player('Alice')
        players = self.store.save_player('Bob')[0]
        self.a, self.b = [p['id'] for p in players]

    def create(self, target=7):
        return self.store.create_live_match(self.a, self.b, target)[1][-1]

    def point(self, match, player):
        snapshot = self.store.score_live_match(match['id'], player, match['revision'])
        return next(m for m in snapshot[1] if m['id'] == match['id'])

    def test_resume_log_and_completion_order(self):
        match = self.create(2)
        self.store.register(self.a, self.b, 11, 5)
        match = self.point(match, self.b)
        players, matches, _ = Store(self.temp.name).snapshot()
        self.assertEqual(matches[0]['point_log'], match['point_log'])
        self.assertEqual(sum(row['matches'] for row in standings(players, matches)), 2)
        match = self.point(match, self.b)
        players, matches, _ = Store(self.temp.name).snapshot()
        self.assertEqual(matches[-1]['id'], match['id'])
        self.assertEqual(match['status'], 'completed')
        rows = {r['player']['id']: r for r in standings(players, matches)}
        self.assertGreater(rows[self.b]['elo'], 1000)
        self.assertEqual(rows[self.b]['last5'], [False, True])
        self.assertEqual([p['player'] for p in matches[-1]['point_log']], [self.b, self.b])
        with (Path(self.temp.name) / 'matches.csv').open(newline='') as handle:
            saved = list(csv.DictReader(handle))[-1]
        self.assertEqual(json.loads(saved['point_log']), match['point_log'])
        with self.assertRaisesRegex(StoreError, 'winner'):
            self.point(match, self.a)

    def test_overtime_undo_and_undo_winner_recalculates_elo(self):
        match = self.create(2)
        for player in (self.a, self.b, self.a, self.b):
            match = self.point(match, player)
        self.assertEqual(live_state(match)['overtime_round'], 2)
        match = self.point(match, None)
        self.assertEqual(live_state(match)['overtime_score'], (1, 0))
        match = self.point(match, self.a)
        self.assertEqual(match['status'], 'completed')
        self.assertEqual([r['elo'] for r in standings(*self.store.snapshot()[:2])], [1016, 984])
        match = self.point(match, None)
        self.assertEqual(match['status'], 'in_progress')
        rows = standings(*self.store.snapshot()[:2])
        self.assertTrue(all(r['matches'] == 0 and r['elo'] == 1000 and r['last5'] == [] for r in rows))
        self.assertEqual(len(match['point_log']), 3)

    def test_stale_revision_remains_stale_after_undo(self):
        match = self.create()
        match = self.point(match, self.a)
        match = self.point(match, None)
        self.assertEqual(match['point_log'], [])
        self.assertEqual(match['revision'], 2)
        with self.assertRaisesRegex(StoreError, 'another computer'):
            self.store.score_live_match(match['id'], self.b, 0)

    def test_failed_point_write_preserves_log_score_and_revision(self):
        match = self.create()
        before = (Path(self.temp.name) / 'matches.csv').read_bytes()
        with patch('storage.os.replace', side_effect=OSError('NAS unavailable')):
            with self.assertRaises(OSError):
                self.point(match, self.a)
        self.assertEqual((Path(self.temp.name) / 'matches.csv').read_bytes(), before)
        self.assertEqual(Store(self.temp.name).snapshot()[1][-1], match)

    def test_validation_and_retired_player_can_finish(self):
        for a, b, target in [(self.a, self.a, 7), (self.a, self.b, 1), (self.a, self.b, 2.5), (self.a, 'unknown', 7)]:
            with self.assertRaises(StoreError):
                self.store.create_live_match(a, b, target)
        match = self.create(2)
        with self.assertRaises(StoreError):
            self.point(match, 'unknown')
        with self.assertRaises(StoreError):
            self.point(match, None)
        self.store.set_active(self.a, False)
        with self.assertRaises(StoreError):
            self.create()
        match = self.point(match, self.a)
        match = self.point(match, self.a)
        self.assertEqual(match['status'], 'completed')

    def test_tournament_version_csv_is_preserved_on_read_and_upgraded_on_save(self):
        path = Path(self.temp.name) / 'matches.csv'
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=Store.TOURNAMENT_MATCH_FIELDS)
            writer.writeheader()
            writer.writerow(dict(id='old', timestamp='2026-09-17T00:00:00+00:00', player1=self.a,
                                 player2=self.b, score1=11, score2=8, tournament_id='', fixture_id=''))
        before = path.read_bytes()
        self.assertEqual(self.store.snapshot()[1][0]['status'], 'completed')
        self.assertEqual(path.read_bytes(), before)
        self.create()
        self.assertEqual(self.store.snapshot()[1][0]['id'], 'old')
        self.assertIsNone(self.store.snapshot()[1][0]['target_points'])

    def test_corrupt_live_log_is_rejected_without_overwrite(self):
        self.create()
        players, matches, _ = self.store.snapshot()
        matches[0]['score1'] = 8
        self.store._write('matches.csv', Store.MATCH_FIELDS, matches)
        before = (Path(self.temp.name) / 'matches.csv').read_bytes()
        with self.assertRaisesRegex(StoreError, 'point log'):
            self.store.register(self.a, self.b, 11, 4)
        self.assertEqual((Path(self.temp.name) / 'matches.csv').read_bytes(), before)

    def test_concurrent_clients_cannot_both_score_from_same_revision(self):
        match = self.create()
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        processes = [ctx.Process(target=concurrent_point,
                                 args=(self.temp.name, match['id'], player, queue)) for player in (self.a, self.b)]
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
        self.assertTrue(any('another computer' in result for result in results))
        self.assertEqual(len(self.store.snapshot()[1][0]['point_log']), 1)


class LiveUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, window):
        deadline = time.monotonic() + 10
        while window.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(window.worker)

    def test_create_score_resume_finish_and_view_point_log(self):
        from app import Window
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QTableWidget
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save_player('Alice')
            players = store.save_player('Bob')[0]
            window = Window(store)
            self.wait(window)
            window.match_mode.setCurrentIndex(1)
            live = window.live_view
            live.player1.setCurrentIndex(live.player1.findData(players[0]['id']))
            live.player2.setCurrentIndex(live.player2.findData(players[1]['id']))
            live.target.setValue(2)
            live.create_button.click()
            self.wait(window)
            self.assertEqual(window.history.rowCount(), 0)
            self.assertIn('0 matches', window.summary.text())
            self.assertIsNone(live.player1.currentData())
            live.point1.click()
            self.wait(window)
            self.assertIn('1 – 0', live.score.text())
            window.close()
            window = Window(Store(directory))
            self.wait(window)
            live = window.live_view
            self.assertIn('1 – 0', live.score.text())
            live.point1.click()
            self.wait(window)
            self.assertIn('Winner: Alice', live.phase.text())
            self.assertFalse(live.point1.isEnabled())
            self.assertEqual(window.history.rowCount(), 1)
            self.assertEqual(window.ranking.item(0, 2).text(), '1016')
            captured = []

            def inspect():
                dialog = self.app.activeModalWidget()
                captured.append(dialog.findChild(QTableWidget).rowCount())
                dialog.reject()

            QTimer.singleShot(0, inspect)
            live.point_history()
            self.assertEqual(captured, [2])
            window.close()
