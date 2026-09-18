import unittest

from ranking import database_stats


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
        self.assertEqual(result['average_elo'], 1000)
        self.assertEqual(result['median_elo'], 1000)

    def test_regular_match_and_excluded_results(self):
        result = database_stats(self.players, [self.match('aaa', target=3)])
        self.assertEqual(result['overall']['server'], dict(wins=3, total=3))
        self.assertEqual(result['overall']['match_point'], dict(wins=1, total=1))
        for match in (self.match('a', status='in_progress'), self.match('aa', target=None)):
            result = database_stats(self.players, [match])
            self.assertEqual(result['overall']['server'], dict(wins=0, total=0))

    def test_empty_database(self):
        result = database_stats([], [])
        self.assertIsNone(result['average_elo'])
        self.assertIsNone(result['median_elo'])
