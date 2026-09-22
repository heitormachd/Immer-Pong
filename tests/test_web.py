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

    def test_change_checks_do_not_load_or_lock_the_ledger(self):
        before = self.ledger()
        with patch.object(self.store, '_load', side_effect=AssertionError('CSV read')):
            with patch.object(self.store, '_locked', side_effect=AssertionError('Lock acquired')):
                first = self.client.get('/changes')
                second = self.client.get('/changes')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json, second.json)
        self.assertEqual(first.headers['Cache-Control'], 'no-store')
        self.assertEqual(before, self.ledger())
        self.assertIn(f'data-version="{first.json["version"]}"', self.client.get('/').text)

    def test_change_checks_detect_each_ledger_and_restart(self):
        version = self.client.get('/changes').json['version']
        for mutate in (
            lambda: self.store.save_player('Eve'),
            lambda: self.store.register(self.ids[0], self.ids[1], 7, 1),
            lambda: self.post('/tournaments', name='Cup', system='single', participants=self.ids[:2]),
            lambda: (Path(self.temp.name) / 'tournaments.csv').unlink(),
        ):
            mutate()
            updated = self.client.get('/changes').json['version']
            self.assertNotEqual(version, updated)
            version = updated
        # Use a separate valid empty ledger to check restart invalidation.
        directory = Path(self.temp.name) / 'restart'
        first = create_app(directory).test_client().get('/changes').json
        second = create_app(directory).test_client().get('/changes').json
        self.assertNotEqual(first, second)

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

    def test_stats_and_head_to_head_are_read_only(self):
        a, b, c = self.ids[:3]
        self.store.register(a, b, 7, 3)
        self.store.register(b, a, 7, 2)
        self.store.register(a, c, 7, 0)
        self.store.set_active(b, False)
        before = self.ledger()
        page = self.client.get('/stats')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Overall Stats', page.text)
        self.assertIn('1008.03', page.text)
        self.assertNotIn('Average Elo', page.text)
        self.assertIn('no recorded opportunities', page.text)
        page = self.client.get('/stats', query_string=dict(section='player', player=a, opponent=b))
        self.assertEqual(page.status_code, 200)
        self.assertIn('<dt>Matches played</dt><dd><strong>2</strong>', page.text)
        self.assertIn('50.0%', page.text)
        self.assertIn('66.7%', page.text)
        self.assertIn('Points winrate', page.text)
        self.assertNotIn('Points for', page.text)
        self.assertNotIn('Points against', page.text)
        self.assertIn('Performance expectation', page.text)
        self.assertIn('<dt>Clutch</dt>', page.text)
        self.assertIn('no complete point histories', page.text)
        self.assertNotIn('<th>Time</th>', page.text)
        self.assertIn('Bob (retired)', page.text)
        self.assertEqual(self.client.get('/stats?section=player').status_code, 200)
        self.assertEqual(self.client.get('/stats?player=missing').status_code, 404)
        self.assertEqual(self.client.get('/stats?section=invalid').status_code, 400)
        self.assertEqual(before, self.ledger())

    def test_duel_rates_exclude_other_opponents_and_unfinished_matches(self):
        a, b, c = self.ids[:3]
        for opponent, sequence in ((b, (a, b, a, b, b, b)), (c, (a, a))):
            match = self.store.create_live_match(a, opponent, 2)[1][-1]
            for revision, player in enumerate(sequence):
                self.store.score_live_match(match['id'], player, revision)
        self.store.create_live_match(a, b, 2)
        page = self.client.get('/stats', query_string=dict(section='player', player=a, opponent=b))
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('Alice vs Bob', page.text)
        duel = page.text
        self.assertIn('<dt>Matches played</dt><dd><strong>1</strong>', duel)
        self.assertIn('33.3%', duel)  # Alice wins 1 of 3 serves against Bob.
        self.assertIn('0/2', duel)  # Alice converts neither match point.
        self.assertNotIn('Bob in this duel', duel)

    def test_player_clutch_displays_score(self):
        a, b = self.ids[:2]
        match = self.store.create_live_match(a, b, 3)[1][-1]
        for revision, player in enumerate((b, a, a, a)):
            self.store.score_live_match(match['id'], player, revision)
        before = self.ledger()
        page = self.client.get('/stats', query_string=dict(section='player', player=a))
        self.assertEqual(page.status_code, 200)
        self.assertIn('+3.6 pp', page.text)
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

    def test_live_elo_estimates_follow_current_ratings(self):
        a, b = self.ids[:2]
        response = self.post('/', action='create_live', player1=a, player2=b, target=2)
        path = response.location
        before = self.ledger()
        page = self.client.get(path).text
        self.assertEqual(page.count('50.0%'), 2)
        self.assertEqual(page.count('+16.00'), 2)
        self.assertNotIn('-16.00', page)
        self.assertEqual(before, self.ledger())

        self.store.register(a, b, 7, 0)
        page = self.client.get(path).text
        left, center_and_right = page.split('<div class="scoreboard">', 1)
        for value in ('Elo 1016.00', '54.6%', '+14.53 Elo if win'):
            self.assertIn(value, left)
        for value in ('Elo 984.00', '45.4%', '+17.47 Elo if win'):
            self.assertIn(value, center_and_right)
        self.assertNotIn('-14.53', page)
        self.assertNotIn('-17.47', page)
        self.post(path, action='point', player_id=a, revision=0)
        self.assertIn('54.6%', self.client.get(path).text)
        self.post(path, action='point', player_id=a, revision=1)
        self.assertNotIn('Win probability', self.client.get(path).text)
        self.assertIn('(+14.53)', self.client.get('/').text)
        self.post(path, action='undo', revision=2)
        self.assertIn('54.6%', self.client.get(path).text)

    def test_live_scoring_overtime_stale_submission_finish_and_undo(self):
        a, b = self.ids[:2]
        response = self.post('/', action='create_live', player1=a, player2=b, target=2)
        self.assertEqual(response.status_code, 303)
        path = response.location
        self.assertEqual(self.client.get(path).status_code, 200)
        # 1–1 starts overtime, A/B resets it, B/B wins the second round.
        for revision, player in enumerate((a, b, a, b, b, b)):
            response = self.post(path, action='point', player_id=player, revision=revision)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.location, '/' if revision == 5 else path)
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

    def test_ace_points_are_saved_displayed_and_undone(self):
        a, b = self.ids[:2]
        path = self.post('/', action='create_live', player1=a, player2=b, target=2).location
        page = self.client.get(path).text
        self.assertEqual(page.count('>ACE!</button>'), 2)
        for revision, player in enumerate((b, a, a, a)):
            response = self.post(path, action='point', player_id=player, revision=revision, ace='1')
            self.assertEqual(response.status_code, 303)
        match = self.store.snapshot()[1][0]
        self.assertEqual((match['score1'], match['score2'], match['status']), (3, 1, 'completed'))
        self.assertTrue(all(point['ace'] for point in match['point_log']))
        self.assertEqual(self.client.get(path).text.count('<td>ACE!</td>'), 4)
        self.assertEqual(self.post(path, action='point', player_id=b, revision=4, ace='1').status_code, 409)
        self.assertEqual(self.post(path, action='undo', revision=4).status_code, 303)
        self.assertEqual(self.client.get(path).text.count('<td>ACE!</td>'), 3)
        self.assertEqual(self.post(path, action='point', player_id=a, revision=5).status_code, 303)
        match = self.store.snapshot()[1][0]
        self.assertNotIn('ace', match['point_log'][-1])
        self.assertEqual(self.client.get(path).text.count('<td>ACE!</td>'), 3)

    def test_round_robin_ignores_finals_scoring_options(self):
        response = self.post('/tournaments', name='League', system='round_robin',
                             participants=self.ids, target_stage='final',
                             alternate_target='invalid', bo3_stage='final')
        self.assertEqual(response.status_code, 303)
        tournament = self.store.snapshot()[2][-1]
        self.assertEqual(tournament['target_stage'], 'all')
        self.assertEqual(tournament['bo3_stage'], 'none')
        self.assertEqual(tournament['alternate_target'], 11)

    def test_tournament_matches_use_live_scoring_and_unlock_next_fixture(self):
        a, b = self.ids[:2]
        response = self.post('/tournaments', name='Live cup', system='single',
                             participants=[a, b], groups=1, target=2)
        tournament_path = response.location
        tournament = self.store.snapshot()[2][-1]
        fixture = next(f for f in tournament_state(tournament, [])["fixtures"]
                       if not f['bye'] and not f['result'])

        page = self.client.get(tournament_path)
        self.assertIn('Next match', page.text)
        self.assertIn('Start live match', page.text)
        self.assertNotIn('Register result', page.text)
        self.assertNotIn('<h4>Bracket</h4>', page.text)

        response = self.post(tournament_path, action='start_live', fixture_id=fixture['id'],
                             player1=fixture['player1'], player2=fixture['player2'])
        self.assertEqual(response.status_code, 303)
        live_path = response.location
        self.assertIn('Open live score', self.client.get(tournament_path).text)

        self.assertEqual(self.post(live_path, action='point', player_id=a, revision=0).status_code, 303)
        response = self.post(live_path, action='point', player_id=a, revision=1)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, tournament_path)
        self.assertTrue(tournament_state(tournament, self.store.snapshot()[1])['complete'])
        completed_page = self.client.get(tournament_path).text
        self.assertNotIn('Next match', completed_page)

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

    def test_odd_group_double_creation_and_playoff_bye(self):
        for name in ('Eve', 'Frank', 'Grace', 'Helen', 'Ivan'):
            self.store.save_player(name)
        ids = [p['id'] for p in self.store.snapshot()[0]]
        response = self.post('/tournaments', name='Odd cup', system='group_double',
                             participants=ids, groups=3)
        self.assertEqual(response.status_code, 303, response.text)
        tournament = self.store.snapshot()[2][-1]
        for fixture in tournament_state(tournament, [])['fixtures']:
            self.store.register_tournament_match(tournament['id'], fixture['id'], 7, 2,
                                                 (fixture['player1'], fixture['player2']))
        state = tournament_state(tournament, self.store.snapshot()[1])
        bye = next(f for f in state['fixtures'] if f['id'] == 'u1-1')
        self.assertTrue(bye['bye'])
        page = self.client.get(response.location)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Bye', page.text)
        self.assertIn('Next match', page.text)

    def test_major_titles_only_awarded_to_champion_and_removed_on_undo(self):
        a, b = self.ids[:2]
        for classification in ('minor', 'major'):
            response = self.post('/tournaments', name=f'{classification} title', system='single',
                                 participants=[a, b], classification=classification, badge='star')
            self.assertEqual(response.status_code, 303)
            event = self.store.snapshot()[2][-1]
            self.assertEqual((event['classification'], event['badge']), (classification, 'star' if classification == 'major' else ''))
            self.assertNotIn('class="achievement"', self.client.get(
                '/stats', query_string=dict(section='player', player=a)).text)
            fixture = tournament_state(event, [])['fixtures'][0]
            self.store.register_tournament_match(event['id'], fixture['id'], 7, 2,
                                                  (fixture['player1'], fixture['player2']))
            winner, loser = fixture['player1'], fixture['player2']
            page = self.client.get('/stats', query_string=dict(section='player', player=winner)).text
            self.assertEqual('class="achievement"' in page, classification == 'major')
            self.assertNotIn('class="achievement"', self.client.get(
                '/stats', query_string=dict(section='player', player=loser)).text)
            if classification == 'major':
                self.assertIn(f'href="{response.location}"', page)
                self.assertIn('/static/badges/star.svg', page)
                self.store.delete_match(self.store.snapshot()[1][-1]['id'])
                self.assertNotIn('class="achievement"', self.client.get(
                    '/stats', query_string=dict(section='player', player=winner)).text)

    def test_minor_ignores_badge_and_upload_and_displays_no_badge(self):
        from io import BytesIO
        response = self.post('/tournaments', name='Minor without badge', system='single',
                             participants=self.ids[:2], classification='minor', badge='shield',
                             image=(BytesIO(b'ignored image'), 'upload.png'))
        self.assertEqual(response.status_code, 303)
        event = self.store.snapshot()[2][-1]
        self.assertEqual(event['badge'], '')
        self.assertFalse((Path(self.temp.name) / 'badges').exists())
        self.assertNotIn('<img', self.client.get(response.location).text)
        page = self.client.get('/tournaments').text
        row = next(row for row in page.split('<tr>') if 'Minor without badge' in row)
        self.assertNotIn('<img', row.split('</tr>')[0])
        fixture = tournament_state(event, [])['fixtures'][0]
        self.store.register_tournament_match(event['id'], fixture['id'], 7, 0,
                                             (fixture['player1'], fixture['player2']))
        page = self.client.get('/tournaments').text
        row = next(row for row in page.split('<tr>') if 'Minor without badge' in row)
        self.assertNotIn('<img', row.split('</tr>')[0])

    def test_badge_upload_is_normalized_persisted_and_served(self):
        from io import BytesIO
        from PIL import Image
        image = BytesIO()
        Image.new('RGB', (900, 600), 'blue').save(image, 'JPEG')
        image.seek(0)
        response = self.post('/tournaments', name='Custom cup', system='single',
                             participants=self.ids[:2], classification='major', badge='cup',
                             image=(image, '../../custom.jpg'))
        self.assertEqual(response.status_code, 303, response.text)
        event = self.store.snapshot()[2][-1]
        self.assertTrue(event['badge'].endswith('.png'))
        page = self.client.get(response.location)
        self.assertIn('/badges/' + event['badge'], page.text)
        asset = self.client.get('/badges/' + event['badge'])
        self.assertEqual(asset.mimetype, 'image/png')
        with Image.open(BytesIO(asset.data)) as normalized:
            self.assertEqual(normalized.size, (512, 341))
        asset.close()
        restarted = create_app(self.temp.name).test_client()
        asset = restarted.get('/badges/' + event['badge'])
        self.assertEqual(asset.status_code, 200)
        asset.close()

    def test_invalid_badges_and_uploads_do_not_create_tournaments(self):
        from io import BytesIO
        from PIL import Image
        before = self.ledger()
        for extra in (dict(classification='legend'), dict(classification='major', badge='../invalid'),
                      dict(classification='major', image=(BytesIO(b'<svg></svg>'), 'fake.png')),
                      dict(classification='major', image=(BytesIO(b'x' * (5 * 1024 * 1024 + 1)), 'large.png'))):
            response = self.post('/tournaments', name='Bad cup', system='single',
                                 participants=self.ids[:2], **extra)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(before, self.ledger())
        image = BytesIO()
        Image.new('RGB', (10, 10)).save(image, 'PNG')
        image.seek(0)
        response = self.post('/tournaments', name='', system='single',
                             participants=self.ids[:2], classification='major', image=(image, 'valid.png'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(list((Path(self.temp.name) / 'badges').glob('*')), [])
        response = self.post('/tournaments', image=(BytesIO(b'x' * (6 * 1024 * 1024)), 'huge.png'))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(before, self.ledger())

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
            self.assertIn(f'data-changes-url="{prefix}/changes"', response.text)
            self.assertEqual(client.get(prefix + '/changes').status_code, 200)
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
