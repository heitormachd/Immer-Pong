import unittest

from ranking import database_stats, rebuild_match_elos


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

    def test_totals_performance_and_median_use_completed_results(self):
        matches = [dict(id='1', player1='a', player2='b', score1=7, score2=2),
                   dict(id='2', player1='c', player2='a', score1=7, score2=4),
                   self.match('a', status='in_progress')]
        rebuild_match_elos(matches)
        result = database_stats(self.players, matches)
        a = result['personal']['a']
        expected_second = 1 / (1 + 10 ** (-16 / 400))
        self.assertEqual((a['matches'], a['wins'], a['scored'], a['conceded']), (2, 1, 11, 9))
        self.assertAlmostEqual(a['performance'], 100 * (0.5 - expected_second) / 2)
        self.assertAlmostEqual(result['personal']['c']['performance'], 100 * expected_second)
        # Only A has two matches, so B/C's ratings must not affect the median.
        self.assertAlmostEqual(result['median_elo'], 1016 - 32 * expected_second)

    def test_performance_uses_saved_ratings_even_for_a_subset(self):
        match = self.match('aa')
        match.update(elo1_before=800.0, elo2_before=1200.0)
        result = database_stats(self.players, [match])
        self.assertAlmostEqual(result['personal']['a']['performance'], 100 * 10 / 11)
        self.assertAlmostEqual(result['personal']['b']['performance'], -100 * 10 / 11)
        self.assertIsNone(result['personal']['c']['performance'])
