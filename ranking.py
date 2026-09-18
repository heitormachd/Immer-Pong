"""Rebuild standings from the match ledger, in registration order."""

from statistics import median

from live_scoring import live_state


def elo_win_probability(elo1, elo2):
    return 1 / (1 + 10 ** ((elo2 - elo1) / 400))


def elo_change(elo1, elo2, won):
    return 32 * (int(won) - elo_win_probability(elo1, elo2))


def match_elo_estimates(players, matches, match):
    stats, _ = _replay(players, matches)
    a, b = match['player1'], match['player2']
    return {player: dict(elo=stats[player]['elo'],
                         probability=elo_win_probability(stats[player]['elo'], stats[opponent]['elo']),
                         win=elo_change(stats[player]['elo'], stats[opponent]['elo'], True),
                         loss=elo_change(stats[player]['elo'], stats[opponent]['elo'], False))
            for player, opponent in ((a, b), (b, a))}


def rebuild_match_elos(matches):
    """Refresh persisted pre-match ratings in ledger completion order before saving."""
    ratings = {}
    for match in matches:
        if match.get('status', 'completed') != 'completed':
            match['elo1_before'] = match['elo2_before'] = None
            continue
        a, b = match['player1'], match['player2']
        x, y = ratings.get(a, 1000.0), ratings.get(b, 1000.0)
        match['elo1_before'], match['elo2_before'] = x, y
        change = elo_change(x, y, match['score1'] > match['score2'])
        ratings[a], ratings[b] = x + change, y - change


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

    summaries, changes = _replay(players, matches)
    performance = {p['id']: 0.0 for p in players}
    for match in matches:
        if match.get('status', 'completed') == 'completed':
            change = changes[match['id']]['player1']['change']
            performance[match['player1']] += change / 32
            performance[match['player2']] -= change / 32
    for player_id, row in summaries.items():
        personal[player_id].update(matches=row['matches'], wins=row['wins'],
                                   scored=row['scored'], conceded=row['conceded'],
                                   performance=100 * performance[player_id] / row['matches']
                                   if row['matches'] else None)
    ratings = [row['elo'] for row in summaries.values() if row['matches'] > 1]
    return dict(overall=overall, personal=personal,
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
        # Stored values avoid rebuilding rating history on reads. The fallback
        # supports legacy snapshots and callers supplying unsaved matches.
        before_a = match.get('elo1_before', a['elo'])
        before_b = match.get('elo2_before', b['elo'])
        change = elo_change(before_a, before_b, x > y)
        changes[match['id']] = {
            'player1': {'before': before_a, 'change': change},
            'player2': {'before': before_b, 'change': -change},
        }
        a['elo'] = before_a + change
        b['elo'] = before_b - change
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
    completed = [m for m in matches if m.get('status', 'completed') == 'completed']
    if any('elo1_before' not in m for m in completed):
        return _replay(players, matches)[1]
    history = {}
    for match in completed:
        a, b = match['elo1_before'], match['elo2_before']
        change = elo_change(a, b, match['score1'] > match['score2'])
        history[match['id']] = dict(player1=dict(before=a, change=change),
                                    player2=dict(before=b, change=-change))
    return history


def standings(players, matches):
    stats, _ = _replay(players, matches)
    active = [s for s in stats.values() if s['player']['active']]
    active.sort(key=lambda s: (not bool(s['matches']), -s['elo'],
                               s['player']['name'].casefold()))
    for rank, row in enumerate(active, 1):
        row['rank'] = rank if row['matches'] else None
        row['last5'] = row['recent'][-5:]
    return active
