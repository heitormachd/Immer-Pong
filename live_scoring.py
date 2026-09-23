"""Replay live points, including the club's resetting two-point overtime rounds."""


def live_state(match):
    target = match['target_points']
    if type(target) is not int or target < 2:
        raise ValueError('The target must be a whole number of at least 2 points')
    players = (match['player1'], match['player2'])
    if players[0] == players[1]:
        raise ValueError('Choose two different players')
    if not isinstance(match['point_log'], list):
        raise ValueError('Invalid point log')
    best_of = match.get('best_of', 1)
    if best_of not in (1, 3):
        raise ValueError('Invalid best-of format')
    games = [0, 0]
    total, overtime_score = [0, 0], [0, 0]
    overtime_round, winner, history = 0, None, []
    for number, event in enumerate(match['point_log'], 1):
        if (not isinstance(event, dict) or set(event) not in ({'player', 'timestamp'}, {'player', 'timestamp', 'ace'})
                or type(event.get('ace', False)) is not bool
                or event['player'] not in players or not isinstance(event['timestamp'], str)):
            raise ValueError('Invalid point event')
        if winner is not None:
            raise ValueError('The point log continues after the match was won')
        side = players.index(event['player'])
        total[side] += 1
        reset = False
        played_round = overtime_round
        if overtime_round:
            overtime_score[side] += 1
            if overtime_score[side] == 2:
                winner = players[side]
            elif overtime_score == [1, 1]:
                reset = True
                overtime_score = [0, 0]
                overtime_round += 1
        elif total == [target - 1, target - 1]:
            overtime_round = 1
        elif total[side] == target:
            winner = players[side]
        game_number = sum(games) + 1
        game_winner = winner
        if game_winner is not None:
            games[players.index(game_winner)] += 1
            if games[players.index(game_winner)] < best_of // 2 + 1:
                winner = None
        history.append(dict(game=game_number, number=number, player=event['player'], timestamp=event['timestamp'],
                            ace=event.get('ace', False), score1=total[0], score2=total[1], overtime_round=played_round,
                            overtime_score=tuple(overtime_score), reset=reset))
        if game_winner is not None and winner is None:
            total, overtime_score = [0, 0], [0, 0]
            overtime_round = 0
    return dict(score1=games[0] if best_of == 3 else total[0],
                score2=games[1] if best_of == 3 else total[1], winner=winner,
                game_score=tuple(total), games=tuple(games), best_of=best_of,
                overtime_round=overtime_round, overtime_score=tuple(overtime_score), history=history)


def individual_matches(matches):
    """Expose completed Bo3 games without duplicating the stored series log."""
    if not any(m.get('best_of', 1) == 3 and m.get('target_points') for m in matches):
        return matches
    results = []
    for match in matches:
        if match.get('best_of', 1) != 3 or not match.get('target_points'):
            results.append(dict(match))
            continue
        state = live_state(match)
        completed = sum(state['games'])
        start = 0
        for game in range(1, completed + 1):
            points = [p for p in state['history'] if p['game'] == game]
            end = start + len(points)
            results.append(dict(match, id=f"{match['id']}:game:{game}",
                                series_id=match['id'], game=game, best_of=1,
                                score1=points[-1]['score1'], score2=points[-1]['score2'],
                                timestamp=points[-1]['timestamp'], status='completed',
                                first_server=(match['first_server'] if start == 0
                                              else match['point_log'][start - 1]['player']),
                                point_log=match['point_log'][start:end]))
            start = end
    results.sort(key=lambda m: m['timestamp'])
    # Series-level cached ratings are not per-game ratings. Replay the games.
    for match in results:
        match.pop('elo1_before', None)
        match.pop('elo2_before', None)
    return results
