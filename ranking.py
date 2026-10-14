"""Rebuild standings from the match ledger, in registration order."""

from statistics import median
from math import comb, exp, log, pi, sqrt
from datetime import date, datetime, timezone

from live_scoring import individual_matches, live_state


_GLICKO_SCALE = 173.7178
_GLICKO_TAU = 0.5
_GLICKO_EPSILON = 0.000001


def _glicko2_f(x, delta, phi, variance, a, tau):
    ex = exp(x)
    return (ex * (delta * delta - phi * phi - variance - ex)
            / (2 * (phi * phi + variance + ex) ** 2) - (x - a) / tau ** 2)


def _glicko2_volatility(phi, sigma, delta, variance):
    """Apply Glickman’s Illinois root finder to update rating volatility."""
    tau = _GLICKO_TAU
    a = log(sigma * sigma)
    if delta * delta > phi * phi + variance:
        b = log(delta * delta - phi * phi - variance)
    else:
        k = 1
        while _glicko2_f(a - k * tau, delta, phi, variance, a, tau) < 0:
            k += 1
        b = a - k * tau

    fa = _glicko2_f(a, delta, phi, variance, a, tau)
    fb = _glicko2_f(b, delta, phi, variance, a, tau)
    while abs(b - a) > _GLICKO_EPSILON:
        c = a + (a - b) * fa / (fb - fa)
        fc = _glicko2_f(c, delta, phi, variance, a, tau)
        if fc * fb <= 0:
            a, fa = b, fb
        else:
            fa /= 2
        b, fb = c, fc
    return exp(a / 2)


def _rating_period(timestamp):
    """Use UTC calendar weeks as Glicko-2 rating periods."""
    value = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    year, week, _ = value.astimezone(timezone.utc).isocalendar()
    return date.fromisocalendar(year, week, 1).toordinal() // 7


def glicko2_ratings(players, matches):
    """Return Glicko-2 rating, RD, volatility and conservative score per player."""
    ratings = {p['id']: dict(rating=1500.0, deviation=350.0, volatility=0.06,
                             last_period=None, matches=0) for p in players}
    games = individual_matches(matches)
    periods = {}
    for position, match in enumerate(games):
        if match.get('status', 'completed') != 'completed':
            continue
        try:
            period = _rating_period(match['timestamp'])
        except (KeyError, TypeError, ValueError):
            # Legacy in-memory callers may omit timestamps. Preserve their row order.
            period = 10 ** 9 + position
        periods.setdefault(period, []).append(match)

    for period, period_games in sorted(periods.items()):
        outcomes = {player_id: [] for player_id in ratings}
        for match in period_games:
            a, b = match['player1'], match['player2']
            outcomes[a].append((b, float(match['score1'] > match['score2'])))
            outcomes[b].append((a, float(match['score2'] > match['score1'])))

        # Grow uncertainty through skipped weekly periods. A player’s active
        # period is handled by the regular Glicko-2 update below.
        for player_id, state in ratings.items():
            if state['last_period'] is None:
                continue
            gap = period - state['last_period'] - (1 if outcomes[player_id] else 0)
            if gap > 0:
                phi = state['deviation'] / _GLICKO_SCALE
                state['deviation'] = _GLICKO_SCALE * sqrt(
                    phi * phi + gap * state['volatility'] * state['volatility'])
            if not outcomes[player_id]:
                state['last_period'] = period

        before = {player_id: (state['rating'], state['deviation'], state['volatility'])
                  for player_id, state in ratings.items()}
        for player_id, games_for_player in outcomes.items():
            if not games_for_player:
                continue
            rating, deviation, sigma = before[player_id]
            mu, phi = (rating - 1500) / _GLICKO_SCALE, deviation / _GLICKO_SCALE
            terms = []
            for opponent, score in games_for_player:
                opponent_rating, opponent_deviation, _ = before[opponent]
                opponent_mu = (opponent_rating - 1500) / _GLICKO_SCALE
                opponent_phi = opponent_deviation / _GLICKO_SCALE
                g = 1 / sqrt(1 + 3 * opponent_phi * opponent_phi / (pi * pi))
                expected = 1 / (1 + exp(-g * (mu - opponent_mu)))
                terms.append((g, expected, score))
            variance = 1 / sum(g * g * expected * (1 - expected)
                               for g, expected, _ in terms)
            delta = variance * sum(g * (score - expected)
                                   for g, expected, score in terms)
            sigma_prime = _glicko2_volatility(phi, sigma, delta, variance)
            phi_star = sqrt(phi * phi + sigma_prime * sigma_prime)
            phi_prime = 1 / sqrt(1 / (phi_star * phi_star) + 1 / variance)
            mu_prime = mu + phi_prime * phi_prime * sum(
                g * (score - expected) for g, expected, score in terms)
            state = ratings[player_id]
            state.update(rating=_GLICKO_SCALE * mu_prime + 1500,
                         deviation=_GLICKO_SCALE * phi_prime,
                         volatility=sigma_prime, last_period=period,
                         matches=state['matches'] + len(games_for_player))

    current_period = _rating_period(datetime.now(timezone.utc).isoformat())
    for state in ratings.values():
        if state['last_period'] is not None and current_period > state['last_period']:
            gap = current_period - state['last_period']
            phi = state['deviation'] / _GLICKO_SCALE
            state['deviation'] = _GLICKO_SCALE * sqrt(
                phi * phi + gap * state['volatility'] * state['volatility'])
        state['conservative'] = state['rating'] - 2 * state['deviation']
    return ratings


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
    games = individual_matches(matches)
    for match in games:
        if match.get('status', 'completed') != 'completed':
            match['elo1_before'] = match['elo2_before'] = None
            continue
        a, b = match['player1'], match['player2']
        x, y = ratings.get(a, 1000.0), ratings.get(b, 1000.0)
        match['elo1_before'], match['elo2_before'] = x, y
        change = elo_change(x, y, match['score1'] > match['score2'])
        ratings[a], ratings[b] = x + change, y - change

    if games is not matches:
        first_games = {}
        for game in games:
            first_games.setdefault(game.get('series_id', game['id']), game)
        for match in matches:
            first = first_games.get(match['id'])
            for field in ('elo1_before', 'elo2_before'):
                match[field] = first[field] if first and match['status'] == 'completed' else None


def simple_ratings(players, matches):
    """Solve SRS = mean margin + mean opponent SRS, centered per connected group."""
    matches = individual_matches(matches)
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
    matches = individual_matches(matches)
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
        server = match.get('first_server', sides[0])
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
    standing_rows = standings(players, matches)
    standing_by_player = {row['player']['id']: row for row in standing_rows}
    for player_id, row in summaries.items():
        tracked = clutch[player_id]
        standing = standing_by_player.get(player_id, {})
        personal[player_id].update(
            clutch=100 * tracked['residual'] / tracked['weight'] if tracked['weight'] else None,
            clutch_matches=tracked['matches'], rank=standing.get('rank'),
            glicko=standing.get('glicko', 1500.0),
            glicko_rd=standing.get('glicko_rd', 350.0))
        personal[player_id].update(srs=srs[player_id],
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
    matches = individual_matches(matches)
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
    matches = individual_matches(matches)
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
    ratings = glicko2_ratings(players, matches)
    active = [s for s in stats.values() if s['player']['active']]
    for row in active:
        rating = ratings[row['player']['id']]
        row['glicko'] = rating['rating']
        row['glicko_rd'] = rating['deviation']
        row['glicko_lower'] = rating['conservative']
    active.sort(key=lambda s: (not bool(s['matches']), -s['glicko_lower'],
                               s['player']['name'].casefold()))
    for rank, row in enumerate(active, 1):
        row['rank'] = rank if row['matches'] else None
        row['last5'] = row['recent'][-5:]
    return active
