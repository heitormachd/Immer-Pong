"""Rebuild standings from the match ledger, in registration order."""


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
