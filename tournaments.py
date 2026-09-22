"""Deterministic tournament schedules rebuilt from the shared match ledger."""

SYSTEMS = {
    'single': 'Single-Elimination',
    'group_double': 'Double-Elimination with Group stage',
    'round_robin': 'Round-robin',
}


def round_robin(players):
    rotation = list(players)
    if len(rotation) % 2:
        rotation.append(None)
    for round_number in range(1, len(rotation)):
        pairs = [(rotation[i], rotation[-1 - i]) for i in range(len(rotation) // 2)]
        yield round_number, [(a, b) for a, b in pairs if a is not None and b is not None]
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]


def seed_bracket(players):
    """Place top seeds opposite bottom seeds, with first-round byes as needed."""
    seeds = [1, 2]
    while len(seeds) < len(players):
        seeds = [value for seed in seeds for value in (seed, 2 * len(seeds) + 1 - seed)]
    return [players[seed - 1] if seed <= len(players) else None for seed in seeds]


def group_standings(players, fixtures):
    rows = {p: dict(player=p, wins=0, losses=0, scored=0, conceded=0, seed=i)
            for i, p in enumerate(players)}
    for fixture in fixtures:
        result = fixture['result']
        if result is None:
            continue
        for a, x, y in ((result['player1'], result['score1'], result['score2']),
                        (result['player2'], result['score2'], result['score1'])):
            rows[a]['wins'] += int(x > y)
            rows[a]['losses'] += int(x < y)
            rows[a]['scored'] += x
            rows[a]['conceded'] += y
    return sorted(rows.values(), key=lambda row: (-row['wins'],
                  -(row['scored'] - row['conceded']), -row['scored'], row['seed']))


def tournament_state(tournament, matches):
    """Return played fixtures, byes, ready fixtures, standings and champion.

    New elimination rounds unlock only after their prerequisite rounds finish.
    A group's lower half starts playoffs with one loss; group losses themselves
    do not count as elimination losses.
    """
    recorded = [m for m in matches if m.get('tournament_id') == tournament['id']
                and m.get('status', 'completed') == 'completed']
    live_records = [m for m in matches if m.get('tournament_id') == tournament['id']
                    and m.get('status') == 'in_progress']
    results = {m['fixture_id']: m for m in recorded}
    live_matches = {m['fixture_id']: m for m in live_records}
    if len(results) != len(recorded):
        raise ValueError('A tournament fixture has more than one result')
    if len(live_matches) != len(live_records):
        raise ValueError('A tournament fixture has more than one live match')
    if set(results) & set(live_matches):
        raise ValueError('A tournament fixture has both a result and a live match')
    fixtures, tables = [], []

    def game(key, stage, round_number, a, b):
        result = results.get(key)
        live_match = live_matches.get(key)
        if result is not None and (result['player1'], result['player2']) != (a, b):
            raise ValueError('Tournament result does not match its bracket participants')
        if live_match is not None and (live_match['player1'], live_match['player2']) != (a, b):
            raise ValueError('Live tournament match does not match its bracket participants')
        bye = a is None or b is None
        if bye and result is not None:
            raise ValueError('A bye cannot have a recorded score')
        if bye and live_match is not None:
            raise ValueError('A bye cannot have a live match')
        winner = (a or b) if bye else (
            result['player1'] if result['score1'] > result['score2'] else result['player2']
        ) if result else None
        fixture = dict(id=key, stage=stage, round=round_number, player1=a, player2=b,
                       result=result, live_match=live_match, bye=bye, winner=winner)
        fixtures.append(fixture)
        return fixture

    def play_round(pool, key, stage, round_number):
        if len(pool) == 1:
            return pool, []
        pool = list(pool)
        if len(pool) % 2:
            pool.append(None)
        games = [game(f'{key}-{i // 2 + 1}', stage, round_number, pool[i], pool[i + 1])
                 for i in range(0, len(pool), 2)]
        if any(g['winner'] is None for g in games):
            return None, None
        winners = [g['winner'] for g in games]
        losers = [g['player2'] if g['winner'] == g['player1'] else g['player1']
                  for g in games if not g['bye']]
        return winners, losers

    def finish(phase, champion=None):
        fixture_ids = {f['id'] for f in fixtures}
        if (set(results) - fixture_ids) or (set(live_matches) - fixture_ids):
            raise ValueError('Tournament contains a result for a fixture that is not available')
        return dict(fixtures=fixtures, tables=tables, phase=phase,
                    complete=champion is not None, champion=champion)

    players = tournament['players']
    system = tournament['system']
    if system in ('round_robin', 'group_double'):
        groups = [players] if system == 'round_robin' else [
            players[i::tournament['groups']] for i in range(tournament['groups'])]
        for index, group in enumerate(groups, 1):
            label = 'Round-robin' if system == 'round_robin' else f'Group {index}'
            group_games = []
            for number, pairs in round_robin(group):
                group_games.extend(game(f'g{index}-r{number}-m{i}', label, number, a, b)
                                   for i, (a, b) in enumerate(pairs, 1))
            tables.append(dict(label=label, rows=group_standings(group, group_games)))
        if any(f['result'] is None for f in fixtures):
            return finish('Round-robin' if system == 'round_robin' else 'Group stage')
        if system == 'round_robin':
            return finish('Completed', tables[0]['rows'][0]['player'])
        upper, lower = [], []
        # Order playoff seeds by group placing, then group number (not name/Elo).
        for rank in range(max(len(t['rows']) for t in tables)):
            for table in tables:
                if rank < len(table['rows']):
                    target = upper if rank < (len(table['rows']) + 1) // 2 else lower
                    target.append(table['rows'][rank]['player'])
    else:
        upper, lower = players, []

    if system == 'group_double' and len(players) % 2:
        best = min((row for table in tables for row in table['rows']),
                   key=lambda row: (-row['wins'], -(row['scored'] - row['conceded']),
                                    -row['scored'], players.index(row['player'])))['player']
        upper.remove(best)
        upper = seed_bracket([best, *upper])
        # Reserve a first-round bye even when the upper field is a power of two.
        if None not in upper:
            upper = [upper[0], None, upper[1], None, *upper[2:]]
    else:
        upper = seed_bracket(upper)
    number = 1
    while len(upper) > 1:
        winners, dropped = play_round(upper, f'u{number}', 'Upper bracket' if lower else 'Bracket', number)
        if system == 'group_double':
            survivors, _ = play_round(lower, f'l{number}a', 'Lower bracket', 2 * number - 1)
            if winners is None or survivors is None:
                return finish('Playoffs')
            # Interleave lower survivors and upper losers; reverse the drop order
            # to avoid simply matching the same adjacent seeds again.
            dropped.reverse()
            pool = []
            for i in range(max(len(survivors), len(dropped))):
                for side in (survivors, dropped):
                    if i < len(side):
                        pool.append(side[i])
            lower, _ = play_round(pool, f'l{number}b', 'Lower bracket', 2 * number)
            if lower is None:
                return finish('Playoffs')
        elif winners is None:
            return finish('Elimination')
        upper = winners
        number += 1

    if system == 'single':
        return finish('Completed', upper[0])
    # Uneven groups can leave extra lower-bracket survivors after upper finishes.
    while len(lower) > 1:
        lower, _ = play_round(lower, f'lc{number}', 'Lower bracket', 2 * number - 1)
        if lower is None:
            return finish('Playoffs')
        number += 1
    final = game('final', 'Grand final', 1, upper[0], lower[0])
    if final['winner'] is None:
        return finish('Grand final')
    return finish('Completed', final['winner'])
