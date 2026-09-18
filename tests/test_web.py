"""Browser workflows use disposable ledgers, never the office CSV files."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tournaments import tournament_state
from web import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.store = self.app.extensions['store']
        for name in ('Alice', 'Bob', 'Carol', 'Dan'):
            self.store.save_player(name)
        self.ids = [p['id'] for p in self.store.snapshot()[0]]
        self.client.get('/')

    def post(self, path, **data):
        with self.client.session_transaction() as session:
            token = session['csrf']
        return self.client.post(path, data=dict(csrf=token, **data))

    def ledger(self):
        return {p.name: p.read_bytes() for p in Path(self.temp.name).glob('*.csv')}

    def test_pages_are_read_only_and_escape_names(self):
        self.store.save_player('<script>alert(1)</script>')
        before = self.ledger()
        for path in ('/', '/players', '/leaderboards', '/tournaments'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('<script>alert(1)</script>', response.text)
            self.assertIn('&lt;script&gt;', response.text)
        self.assertEqual(before, self.ledger())

    def test_csrf_and_bad_actions_do_not_write(self):
        before = self.ledger()
        data = dict(action='register', player1=self.ids[0], player2=self.ids[1], score1=7, score2=0)
        self.assertEqual(self.client.post('/', data=data).status_code, 400)
        self.assertEqual(self.client.post('/', data=dict(csrf='invalid', **data)).status_code, 400)
        self.assertEqual(self.client.post('/', data=dict(csrf='é', **data)).status_code, 400)
        self.assertEqual(self.post('/', action='unknown').status_code, 400)
        self.assertEqual(before, self.ledger())

    def test_final_score_redirect_resets_form_and_updates_elo(self):
        response = self.post('/', action='register', player1=self.ids[0], player2=self.ids[1], score1=7, score2=3)
        self.assertEqual(response.status_code, 303)
        history = self.client.get(response.location)
        self.assertIn('1000.00', history.text)
        self.assertIn('(+16.00)', history.text)
        self.assertIn('(-16.00)', history.text)
        self.assertIn('value="0" required', history.text)
        self.assertIn('1016', self.client.get('/leaderboards').text)
        match_id = self.store.snapshot()[1][-1]['id']
        self.assertEqual(self.post('/', action='delete', match_id=match_id).status_code, 303)
        self.assertEqual(self.store.snapshot()[1], [])

    def test_invalid_scores_preserve_ledger(self):
        before = self.ledger()
        for x, y in [('7', '7'), ('-1', '3'), ('bad', '1')]:
            response = self.post('/', action='register', player1=self.ids[0], player2=self.ids[1], score1=x, score2=y)
            self.assertEqual(response.status_code, 409)
        self.assertEqual(before, self.ledger())

    def test_players_add_rename_retire_restore(self):
        self.assertEqual(self.post('/players', action='add', name='Eve').status_code, 303)
        eve = self.store.snapshot()[0][-1]['id']
        self.post('/players', action='rename', player_id=eve, name='Eva')
        self.post('/players', action='retire', player_id=eve)
        player = self.store.snapshot()[0][-1]
        self.assertEqual(player['name'], 'Eva')
        self.assertFalse(player['active'])
        self.assertEqual(self.post('/', action='create_live', player1=eve, player2=self.ids[0], target=7).status_code, 409)
        self.post('/players', action='restore', player_id=eve)
        self.assertTrue(self.store.snapshot()[0][-1]['active'])

    def test_live_scoring_overtime_stale_submission_finish_and_undo(self):
        a, b = self.ids[:2]
        response = self.post('/', action='create_live', player1=a, player2=b, target=2)
        self.assertEqual(response.status_code, 303)
        path = response.location
        self.assertEqual(self.client.get(path).status_code, 200)
        # 1–1 starts overtime, A/B resets it, B/B wins the second round.
        for revision, player in enumerate((a, b, a, b, b, b)):
            self.assertEqual(self.post(path, action='point', player_id=player, revision=revision).status_code, 303)
            if revision == 0:
                stale = self.post(path, action='point', player_id=b, revision=0)
                self.assertEqual(stale.status_code, 409)
                self.assertIn('changed on another computer', stale.text)
        match = self.store.snapshot()[1][0]
        self.assertEqual((match['score1'], match['score2'], match['status']), (2, 4, 'completed'))
        page = self.client.get(path).text
        self.assertIn('Winner: Bob', page)
        self.assertIn('Split → reset', page)
        self.assertIn('Undo the winning point?', page)
        self.assertEqual(self.post(path, action='undo', revision=6).status_code, 303)
        self.assertEqual(self.store.snapshot()[1][0]['status'], 'in_progress')
        self.assertIn('0 completed matches', self.client.get('/leaderboards').text)
        self.assertEqual(self.post(path, action='delete').status_code, 303)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_all_tournament_systems_complete_and_reopen(self):
        for system in ('single', 'group_double', 'round_robin'):
            response = self.post('/tournaments', name=system, system=system, participants=self.ids, groups=2)
            self.assertEqual(response.status_code, 303, response.text)
            path = response.location
            tournament = self.store.snapshot()[2][-1]
            count = 0
            while True:
                state = tournament_state(tournament, self.store.snapshot()[1])
                self.assertEqual(self.client.get(path).status_code, 200)
                if state['complete']:
                    break
                fixture = next(f for f in state['fixtures'] if not f['bye'] and not f['result'])
                result = dict(action='result', fixture_id=fixture['id'], player1=fixture['player1'],
                              player2=fixture['player2'], score1=7, score2=2)
                self.assertEqual(self.post(path, **result).status_code, 303)
                self.assertEqual(self.post(path, **result).status_code, 409)
                count += 1
                self.assertLess(count, 40)
            self.assertIn('Winner:', self.client.get(path).text)
            rows = [m for m in self.store.snapshot()[1] if m['tournament_id'] == tournament['id']]
            self.assertEqual(self.post(path, action='undo', match_id=rows[0]['id']).status_code, 409)
            self.assertEqual(self.post(path, action='undo', match_id=rows[-1]['id']).status_code, 303)
            self.assertFalse(tournament_state(tournament, self.store.snapshot()[1])['complete'])
            self.assertEqual(self.client.get('/tournaments').status_code, 200)

    def test_wrong_tournament_undo_cannot_delete_ordinary_match(self):
        self.store.register(self.ids[0], self.ids[1], 7, 0)
        match = self.store.snapshot()[1][-1]
        self.assertEqual(self.post('/tournaments/not-an-event', action='undo', match_id=match['id']).status_code, 409)
        self.assertEqual(len(self.store.snapshot()[1]), 1)

    def test_storage_failure_has_clear_message(self):
        with patch.object(self.store, 'snapshot', side_effect=OSError('test outage')):
            with self.assertLogs(self.app.logger, level='ERROR'):
                response = self.client.get('/')
        self.assertEqual(response.status_code, 503)
        self.assertIn('save may have succeeded', response.text)

    def test_main_and_test_ledgers_and_cookies_are_isolated(self):
        test_app = create_app(Path(self.temp.name) / 'test_data')
        client = test_app.test_client()
        before = self.ledger()
        response = client.get('/players')
        self.assertIn('Test server', response.text)
        self.assertNotEqual(self.app.config['SESSION_COOKIE_NAME'], test_app.config['SESSION_COOKIE_NAME'])
        with client.session_transaction() as session:
            token = session['csrf']
        response = client.post('/players', data=dict(csrf=token, action='add', name='Test only'))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(before, self.ledger())
        self.assertEqual(test_app.extensions['store'].snapshot()[0][0]['name'], 'Test only')

    def test_nas_prefixes_cover_links_assets_redirects_and_posts(self):
        import re
        for prefix in ('/immer-pong', '/ping-pong-test'):
            app = create_app(self.temp.name, url_prefix=prefix)
            client = app.test_client()
            response = client.get(prefix + '/players')
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'href="{prefix}/players"', response.text)
            self.assertIn(f'href="{prefix}/static/style.css"', response.text)
            self.assertIn(f'Path={prefix}/', response.headers['Set-Cookie'])
            token = re.search(r'name="csrf" value="([^"]+)"', response.text)[1]
            result = client.post(prefix + '/players', data=dict(csrf=token, action='add', name=prefix))
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.location, prefix + '/players')
            asset = client.get(prefix + '/static/style.css')
            self.assertEqual(asset.status_code, 200)
            asset.close()
            error = client.post(prefix + '/players', data={'csrf': 'invalid'})
            self.assertEqual(error.status_code, 400)
            self.assertIn(f'href="{prefix}/players"', error.text)
            self.assertEqual(client.get('/players').status_code, 404)

    def test_unknown_resources_and_static_assets(self):
        for path in ('/live/missing', '/tournaments/missing'):
            self.assertEqual(self.client.get(path).status_code, 404)
        for path in ('/static/style.css', '/static/app.js'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()


if __name__ == '__main__':
    unittest.main()
