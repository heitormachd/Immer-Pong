"""Rebuild standings from the match ledger, in registration order."""

from statistics import mean, median

from live_scoring import live_state


def database_stats(players, matches):
    """Point opportunity rates from completed logs; Elo from all completed results."""
    def counters():
        return {key: dict(wins=0, total=0) for key in ('server', 'match_point', 'against')}

    overall = counters()
    personal = {p['id']: counters() for p in players}

    def record(counter, won):
        counter['total'] += 1
        counter['wins'] += int(won)

    for match in matches:
        if match.get('status', 'completed') != 'completed' or not match.get('target_points'):
            continue
        sides = (match['player1'], match['player2'])
        server = sides[0]
        scores, overtime, overtime_score = (0, 0), 0, (0, 0)
        for point in live_state(match)['history']:
            winner = point['player']
            record(overall['server'], winner == server)
            record(personal[server]['server'], winner == server)
            for side, player in enumerate(sides):
                match_point = (overtime_score[side] == 1 if overtime
                               else scores[side] == match['target_points'] - 1)
                if match_point:
                    record(overall['match_point'], winner == player)
                    record(personal[player]['match_point'], winner == player)
                    record(personal[sides[1 - side]]['against'], winner != player)
            server = winner
            scores = (point['score1'], point['score2'])
            overtime_score = point['overtime_score']
            overtime = point['overtime_round'] or int(scores == (match['target_points'] - 1,) * 2)

    ratings = [row['elo'] for row in _replay(players, matches)[0].values()]
    return dict(overall=overall, personal=personal,
                average_elo=mean(ratings) if ratings else None,
                median_elo=median(ratings) if ratings else None)


def _replay(players, matches):
    stats = {
        p['id']: dict(player=p, elo=1000.0, matches=0, wins=0, losses=0,
                      scored=0, conceded=0, recent=[])
        for p in players
    }
    changes = {}
    for match in matches:
        if match.get('status', 'completed') != 'completed':
            continue
        a, b = stats[match['player1']], stats[match['player2']]
        x, y = match['score1'], match['score2']
        expected = 1 / (1 + 10 ** ((b['elo'] - a['elo']) / 400))
        change = 32 * (int(x > y) - expected)
        changes[match['id']] = {
            'player1': {'before': a['elo'], 'change': change},
            'player2': {'before': b['elo'], 'change': -change},
        }
        a['elo'] += change
        b['elo'] -= change
        for row, scored, conceded in ((a, x, y), (b, y, x)):
            row['matches'] += 1
            row['wins'] += int(scored > conceded)
            row['losses'] += int(scored < conceded)
            row['scored'] += scored
            row['conceded'] += conceded
            row['recent'].append(scored > conceded)
    return stats, changes


def match_elo_changes(players, matches):
    """Winner's gain (equal to loser's loss) by completed match ID."""
    return {match_id: abs(values['player1']['change'])
            for match_id, values in match_elo_history(players, matches).items()}


def match_elo_history(players, matches):
    """Pre-match rating and signed change for each player in completed matches."""
    return _replay(players, matches)[1]


def standings(players, matches):
    stats, _ = _replay(players, matches)
    active = [s for s in stats.values() if s['player']['active']]
    active.sort(key=lambda s: (not bool(s['matches']), -s['elo'],
                               s['player']['name'].casefold()))
    for rank, row in enumerate(active, 1):
        row['rank'] = rank if row['matches'] else None
        row['last5'] = row['recent'][-5:]
    return active
