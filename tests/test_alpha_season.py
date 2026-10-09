import tempfile
import unittest

from migrate_alpha_season import migrate
from ranking import standings
from tournaments import tournament_state
from web import create_app


class AlphaSeasonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.app.config['TESTING'] = True
        self.store = self.app.extensions['store']
        for name in ('Alice', 'Bob', 'Carol', 'Dan'):
            self.store.save_player(name)
        self.players = self.store.snapshot()[0]
        self.ids = [p['id'] for p in self.players]
        self.store.register(self.ids[2], self.ids[3], 7, 2)
        self.copa = self.store.create_tournament(
            'Copa Turbo', 'single', self.ids[:2], classification='major')[2][-1]

    def complete_copa(self):
        f = tournament_state(self.copa, [])['fixtures'][0]
        self.store.register_tournament_match(self.copa['id'], f['id'], 7, 3,
                                             (f['player1'], f['player2']))

    def test_incomplete_copa_does_not_reset(self):
        before = self.store.snapshot()
        with self.assertRaises(ValueError):
            migrate(self.temp.name)
        self.assertEqual(self.store.snapshot(), before)
        self.assertIsNone(self.store.alpha_season())

    def test_archive_reset_medals_history_and_new_season(self):
        self.complete_copa()
        players, matches, _ = self.store.snapshot()
        expected = standings(players, matches)
        archive = migrate(self.temp.name)
        self.assertEqual(self.store.snapshot(), (players, [], []))
        self.assertEqual([r['player_id'] for r in archive['standings']],
                         [r['player']['id'] for r in expected])
        self.assertEqual([r['elo'] for r in archive['standings']],
                         [r['elo'] for r in expected])
        self.assertTrue(all(r['elo'] == 1000 and r['rank'] is None
                            for r in standings(players, [])))
        for f in archive['state']['fixtures']:
            if f['result']:
                self.assertEqual(set(f['result']), {'score1', 'score2'})
        client = self.app.test_client()
        overall = client.get('/stats')
        self.assertIn(b'Alpha season', overall.data)
        for row in archive['standings']:
            page = client.get('/stats', query_string=dict(section='player', player=row['player_id']))
            if row['rank'] in (1, 2, 3):
                medal = ('Gold', 'Silver', 'Bronze')[row['rank'] - 1]
                self.assertIn((medal + ' medal').encode(), page.data)
            else:
                self.assertNotIn(b'Alpha season', page.data)
        champion = client.get('/stats', query_string=dict(
            section='player', player=archive['state']['champion']))
        self.assertIn(b'Copa Turbo', champion.data)
        self.assertIn(b'Copa Turbo', client.get('/tournaments').data)
        url = '/tournaments/' + self.copa['id']
        history = client.get(url)
        self.assertEqual(history.status_code, 200)
        self.assertIn(b'Read-only', history.data)
        self.assertNotIn(b'Undo latest result', history.data)
        with client.session_transaction() as session:
            csrf = session['csrf']
        self.assertEqual(client.post(url, data=dict(csrf=csrf, action='undo')).status_code, 400)
        self.store.register(self.ids[3], self.ids[0], 7, 1)
        with self.assertRaises(ValueError):
            migrate(self.temp.name)
        self.assertEqual(len(self.store.snapshot()[1]), 1)
        self.assertEqual(self.store.alpha_season(), archive)
