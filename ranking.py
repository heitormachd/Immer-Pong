"""Rebuild standings from the match ledger, in registration order."""

from statistics import median
from math import comb

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


def simple_ratings(players, matches):
    """Solve SRS = mean margin + mean opponent SRS, centered per connected group."""
    opponents = {p['id']: {} for p in players}
    margins = dict.fromkeys(opponents, 0)
    for match in matches:
        if match.get('status', 'completed') != 'completed':
            continue
        a, b = match['player1'], match['player2']
        margin = match['score1'] - match['score2']
        for player, opponent, difference in ((a, b, margin), (b, a, -margin)):
            opponents[player][opponent] = opponents[player].get(opponent, 0) + 1
            margins[player] += difference
    ratings = dict.fromkeys(opponents)
    remaining = {p for p in opponents if opponents[p]}
    while remaining:
        group, pending = set(), [min(remaining)]
        while pending:
            player = pending.pop()
            if player not in group:
                group.add(player)
                pending.extend(opponents[player].keys() - group)
        remaining -= group
        ids = sorted(group)
        # The schedule Laplacian has one free offset; replace one equation
        # with sum(ratings) = 0 to fix it without an external solver dependency.
        matrix = [[float(sum(opponents[p].values()) if p == q
                         else -opponents[p].get(q, 0)) for q in ids] + [float(margins[p])]
                  for p in ids]
        matrix[-1] = [1.0] * len(ids) + [0.0]
        for column in range(len(ids)):
            pivot = max(range(column, len(ids)), key=lambda r: abs(matrix[r][column]))
            matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
            divisor = matrix[column][column]
            matrix[column] = [v / divisor for v in matrix[column]]
            for row in range(len(ids)):
                if row != column:
                    factor = matrix[row][column]
                    matrix[row] = [v - factor * w for v, w in zip(matrix[row], matrix[column])]
        ratings.update({p: matrix[i][-1] for i, p in enumerate(ids)})
    return ratings


def point_leverage(target, scores, overtime=False):
    """Win-probability swing for a fair next point under our overtime rules."""
    if overtime:
        # A fresh round is 50/50; winning its first point makes it 75/25.
        # The second point either ends the match or returns to 50/50.
        return 0.5
    remaining = 2 * target - sum(scores) - 2
    return comb(remaining, target - scores[0] - 1) / 2 ** remaining


def database_stats(players, matches):
    """Point opportunity rates from completed logs; Elo from all completed results."""
    def counters():
        return {key: dict(wins=0, total=0) for key in ('server', 'match_point', 'against')}

    overall = counters()
    personal = {p['id']: counters() for p in players}
    clutch = {p['id']: dict(residual=0.0, weight=0.0, matches=0) for p in players}

    def record(counter, won):
        counter['total'] += 1
        counter['wins'] += int(won)

    for match in matches:
        if match.get('status', 'completed') != 'completed' or not match.get('target_points'):
            continue
        sides = (match['player1'], match['player2'])
        state = live_state(match)
        complete_log = (state['winner'] is not None
                        and (state['score1'], state['score2']) == (match['score1'], match['score2']))
        if complete_log:
            baseline = state['score1'] / len(state['history'])
            for player in sides:
                clutch[player]['matches'] += 1
        server = sides[0]
        scores, overtime, overtime_score = (0, 0), 0, (0, 0)
        for point in state['history']:
            winner = point['player']
            if complete_log:
                weight = point_leverage(match['target_points'], scores, overtime)
                residual = weight * (int(winner == sides[0]) - baseline)
                for player, value in ((sides[0], residual), (sides[1], -residual)):
                    clutch[player]['residual'] += value
                    clutch[player]['weight'] += weight
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
    srs = simple_ratings(players, matches)
    ranks = {row['player']['id']: row['rank'] for row in standings(players, matches)}
    for player_id, row in summaries.items():
        tracked = clutch[player_id]
        personal[player_id].update(
            clutch=100 * tracked['residual'] / tracked['weight'] if tracked['weight'] else None,
            clutch_matches=tracked['matches'])
        personal[player_id].update(rank=ranks.get(player_id), srs=srs[player_id],
                                   pd=row['scored'] - row['conceded'],
                                   net_points=(row['scored'] - row['conceded']) / row['matches']
                                   if row['matches'] else None, elo=row['elo'], matches=row['matches'], wins=row['wins'],
                                   scored=row['scored'], conceded=row['conceded'],
                                   performance=100 * performance[player_id] / row['matches']
                                   if row['matches'] else None)
    overall.update(matches=sum(row['matches'] for row in summaries.values()) // 2,
                   points=sum(row['scored'] for row in summaries.values()))
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
