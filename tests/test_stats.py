import unittest

from ranking import database_stats, point_leverage, rebuild_match_elos, simple_ratings


class StatsTests(unittest.TestCase):
    players = [dict(id=p, name=p, active=p != 'b') for p in ('a', 'b', 'c')]

    def match(self, points, status='completed', target=2):
        return dict(id='m', player1='a', player2='b', score1=points.count('a'),
                    score2=points.count('b'), status=status, target_points=target,
                    point_log=[dict(player=p, timestamp='') for p in points])

    def test_overtime_resets_and_server_changes(self):
        result = database_stats(self.players, [self.match('ababbb')])
        self.assertEqual(result['overall']['server'], dict(wins=3, total=6))
        self.assertEqual(result['overall']['match_point'], dict(wins=1, total=3))
        self.assertEqual(result['personal']['a']['match_point'], dict(wins=0, total=2))
        self.assertEqual(result['personal']['b']['against'], dict(wins=2, total=2))
        self.assertEqual(result['personal']['a']['against'], dict(wins=0, total=1))
        self.assertNotIn('average_elo', result)
        self.assertIsNone(result['median_elo'])

    def test_player_two_first_server_changes_only_first_point_opportunity(self):
        # A completed B sweep: first service and subsequent services all belong to B.
        match = self.match('bbb', target=3)
        match['first_server'] = 'b'
        result = database_stats(self.players, [match])
        self.assertEqual(result['overall']['server'], dict(wins=3, total=3))
        self.assertEqual(result['personal']['b']['server'], dict(wins=3, total=3))
        self.assertEqual(result['personal']['a']['server'], dict(wins=0, total=0))
        match['first_server'] = 'a'
        result = database_stats(self.players, [match])
        self.assertEqual(result['overall']['server'], dict(wins=2, total=3))
        self.assertEqual(result['personal']['a']['server'], dict(wins=0, total=1))

    def test_regular_match_and_excluded_results(self):
        result = database_stats(self.players, [self.match('aaa', target=3)])
        self.assertEqual(result['overall']['server'], dict(wins=3, total=3))
        self.assertEqual(result['overall']['match_point'], dict(wins=1, total=1))
        for match in (self.match('a', status='in_progress'), self.match('aa', target=None)):
            result = database_stats(self.players, [match])
            self.assertEqual(result['overall']['server'], dict(wins=0, total=0))

    def test_empty_database(self):
        result = database_stats([], [])
        self.assertIsNone(result['median_elo'])
        self.assertEqual((result['overall']['matches'], result['overall']['points']), (0, 0))

    def test_clutch_leverage(self):
        self.assertAlmostEqual(point_leverage(7, (1, 0)), 0.2255859375)
        self.assertAlmostEqual(point_leverage(7, (6, 0)), 1 / 64)
        self.assertEqual(point_leverage(7, (6, 5)), 0.5)
        self.assertEqual(point_leverage(7, (9, 8), overtime=True), 0.5)

    def test_clutch_weights_matches_and_opponents_are_symmetric(self):
        match = self.match('baaa', target=3)
        result = database_stats(self.players, [match])['personal']
        # Weights: 3/8, 3/8, 1/2, 1/2; baseline: 3/4.
        self.assertAlmostEqual(result['a']['clutch'], 100 / 28)
        self.assertAlmostEqual(result['b']['clutch'], -100 / 28)
        sweep = self.match('aaa', target=3)
        sweep['id'] = 'sweep'
        result = database_stats(self.players, [match, sweep])['personal']
        # Sweep adds weight 1 and zero residual, not an equal match vote.
        self.assertAlmostEqual(result['a']['clutch'], 100 / 44)
        self.assertEqual(result['a']['clutch_matches'], 2)
        self.assertIsNone(result['c']['clutch'])

    def test_clutch_overtime_resets(self):
        result = database_stats(self.players, [self.match('abababaa', target=3)])['personal']
        self.assertAlmostEqual(result['a']['clutch'], 100 / 120)
        self.assertEqual(result['a']['clutch_matches'], 1)

    def test_clutch_excludes_missing_incomplete_and_mismatched_logs(self):
        mismatch = self.match('aa')
        mismatch['score1'] = 3
        for match in (self.match('a', status='in_progress'), self.match('aa', target=None),
                      self.match(''), self.match('a'), mismatch):
            with self.subTest(match=match):
                result = database_stats(self.players, [match])['personal']['a']
                self.assertIsNone(result['clutch'])
                self.assertEqual(result['clutch_matches'], 0)

    def test_totals_performance_and_median_use_completed_results(self):
        matches = [dict(id='1', player1='a', player2='b', score1=7, score2=2),
                   dict(id='2', player1='c', player2='a', score1=7, score2=4),
                   self.match('a', status='in_progress')]
        rebuild_match_elos(matches)
        result = database_stats(self.players, matches)
        self.assertEqual((result['overall']['matches'], result['overall']['points']), (2, 20))
        a = result['personal']['a']
        expected_second = 1 / (1 + 10 ** (-16 / 400))
        self.assertEqual((a['matches'], a['wins'], a['scored'], a['conceded']), (2, 1, 11, 9))
        self.assertAlmostEqual(a['performance'], 100 * (0.5 - expected_second) / 2)
        self.assertAlmostEqual(result['personal']['c']['performance'], 100 * expected_second)
        # Only A has two matches, so B/C's ratings must not affect the median.
        self.assertAlmostEqual(result['median_elo'], 1016 - 32 * expected_second)
        self.assertAlmostEqual(a['elo'], 1016 - 32 * expected_second)
        self.assertEqual(a['pd'], 2)
        self.assertEqual(a['net_points'], 1)
        self.assertEqual(a['rank'], 2)
        self.assertIsNone(result['personal']['b']['rank'])

    def test_srs_adjusts_for_schedule_and_centers_separate_groups(self):
        players = [dict(id=p) for p in 'abcdef']
        matches = [dict(player1='a', player2='b', score1=7, score2=3),
                   dict(player1='a', player2='b', score1=7, score2=5),
                   dict(player1='b', player2='c', score1=7, score2=1),
                   dict(player1='d', player2='e', score1=7, score2=3),
                   dict(player1='c', player2='d', score1=0, score2=7, status='in_progress')]
        ratings = simple_ratings(players, matches)
        for player, expected in dict(a=4, b=1, c=-5, d=2, e=-2).items():
            self.assertAlmostEqual(ratings[player], expected)
        self.assertIsNone(ratings['f'])
        self.assertEqual(simple_ratings([], []), {})

    def test_performance_uses_saved_ratings_even_for_a_subset(self):
        match = self.match('aa')
        match.update(elo1_before=800.0, elo2_before=1200.0)
        result = database_stats(self.players, [match])
        self.assertAlmostEqual(result['personal']['a']['performance'], 100 * 10 / 11)
        self.assertAlmostEqual(result['personal']['b']['performance'], -100 * 10 / 11)
        self.assertIsNone(result['personal']['c']['performance'])
