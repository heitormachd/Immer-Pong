"""Rebuild standings from the match ledger, in registration order."""


def standings(players, matches):
    stats = {
        p['id']: dict(player=p, elo=1000.0, matches=0, wins=0, losses=0,
                      scored=0, conceded=0, recent=[])
        for p in players
    }
    for match in matches:
        a, b = stats[match['player1']], stats[match['player2']]
        x, y = match['score1'], match['score2']
        expected = 1 / (1 + 10 ** ((b['elo'] - a['elo']) / 400))
        change = 32 * (int(x > y) - expected)
        a['elo'] += change
        b['elo'] -= change
        for row, scored, conceded in ((a, x, y), (b, y, x)):
            row['matches'] += 1
            row['wins'] += int(scored > conceded)
            row['losses'] += int(scored < conceded)
            row['scored'] += scored
            row['conceded'] += conceded
            row['recent'].append(scored > conceded)
    active = [s for s in stats.values() if s['player']['active']]
    active.sort(key=lambda s: (not bool(s['matches']), -s['elo'],
                               s['player']['name'].casefold()))
    for rank, row in enumerate(active, 1):
        row['rank'] = rank if row['matches'] else None
        row['last5'] = row['recent'][-5:]
    return active
