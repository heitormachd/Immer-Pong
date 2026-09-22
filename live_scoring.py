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
        if (not isinstance(event, dict) or set(event) != {'player', 'timestamp'}
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
                            score1=total[0], score2=total[1], overtime_round=played_round,
                            overtime_score=tuple(overtime_score), reset=reset))
        if game_winner is not None and winner is None:
            total, overtime_score = [0, 0], [0, 0]
            overtime_round = 0
    return dict(score1=games[0] if best_of == 3 else total[0],
                score2=games[1] if best_of == 3 else total[1], winner=winner,
                game_score=tuple(total), games=tuple(games), best_of=best_of,
                overtime_round=overtime_round, overtime_score=tuple(overtime_score), history=history)
