"""Small CSV ledger with one exclusive NAS directory lock per operation."""

import csv
import json
import math
import os
import random
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tournaments import SYSTEMS, tournament_state
from live_scoring import live_state
from ranking import rebuild_match_elos


class StoreError(Exception):
    pass


class Store:
    PLAYER_FIELDS = ('id', 'name', 'active')
    LEGACY_MATCH_FIELDS = ('id', 'timestamp', 'player1', 'player2', 'score1', 'score2')
    TOURNAMENT_MATCH_FIELDS = (*LEGACY_MATCH_FIELDS, 'tournament_id', 'fixture_id')
    LIVE_MATCH_FIELDS = (*TOURNAMENT_MATCH_FIELDS, 'target_points', 'point_log', 'status', 'revision')
    MATCH_FIELDS = (*LIVE_MATCH_FIELDS, 'elo1_before', 'elo2_before')
    TOURNAMENT_FIELDS = ('id', 'name', 'system', 'created_at', 'players', 'groups')

    def __init__(self, directory):
        self.directory = Path(directory)

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / '.write-lock'
        try:
            lock.mkdir()
        except FileExistsError:
            raise StoreError('Shared data is busy. Please try again. If this persists, '
                             'close all app instances and follow lock recovery in README.md.') from None
        try:
            yield
        finally:
            lock.rmdir()

    def _read_csv(self, name, fields):
        try:
            with (self.directory / name).open(newline='', encoding='utf-8') as handle:
                reader = csv.DictReader(handle, strict=True)
                legacy = name == 'matches.csv' and reader.fieldnames in (
                    list(self.LEGACY_MATCH_FIELDS), list(self.TOURNAMENT_MATCH_FIELDS),
                    list(self.LIVE_MATCH_FIELDS))
                if reader.fieldnames != list(fields) and not legacy:
                    raise ValueError(f'Unexpected columns in {name}')
                rows = list(reader)
                if any(set(row) != set(reader.fieldnames) or any(v is None for v in row.values())
                       for row in rows):
                    raise ValueError(f'Incomplete row in {name}')
                if legacy:
                    for row in rows:
                        row.setdefault('tournament_id', '')
                        row.setdefault('fixture_id', '')
                        for key, value in dict(target_points='', point_log='[]',
                                               status='completed', revision='0').items():
                            row.setdefault(key, value)
                return rows
        except FileNotFoundError:
            return []

    def _load(self):
        try:
            players = self._read_csv('players.csv', self.PLAYER_FIELDS)
            matches = self._read_csv('matches.csv', self.MATCH_FIELDS)
            tournaments = self._read_csv('tournaments.csv', self.TOURNAMENT_FIELDS)
            ids, names = set(), set()
            for player in players:
                name = player['name'].strip()
                if (not player['id'] or player['id'] in ids or not name
                        or name.casefold() in names or player['active'] not in ('0', '1')):
                    raise ValueError('Invalid or duplicate player')
                ids.add(player['id'])
                names.add(name.casefold())
                player['name'] = name
                player['active'] = player['active'] == '1'
            tournament_ids = set()
            for tournament in tournaments:
                tournament['players'] = json.loads(tournament['players'])
                tournament['groups'] = int(tournament['groups'])
                participants = tournament['players']
                if (not tournament['id'] or tournament['id'] in tournament_ids
                        or not tournament['name'].strip()
                        or not isinstance(participants, list)
                        or any(not isinstance(p, str) for p in participants)):
                    raise ValueError('Invalid tournament data')
                self._validate_tournament(tournament['system'], participants, tournament['groups'])
                if not set(participants) <= ids:
                    raise ValueError('Tournament references a missing player')
                stamp = datetime.fromisoformat(tournament['created_at'])
                if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
                    raise ValueError('Tournament timestamps must be UTC')
                tournament_ids.add(tournament['id'])
            match_ids = set()
            for match in matches:
                if not match['id'] or match['id'] in match_ids:
                    raise ValueError('Invalid or duplicate match ID')
                match_ids.add(match['id'])
                stamp = datetime.fromisoformat(match['timestamp'])
                if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
                    raise ValueError('Match timestamps must be UTC')
                if match['player1'] not in ids or match['player2'] not in ids:
                    raise ValueError('Match references a missing player')
                match['score1'] = int(match['score1'])
                match['score2'] = int(match['score2'])
                match['target_points'] = int(match['target_points']) if match['target_points'] else None
                match['point_log'] = json.loads(match['point_log'])
                match['revision'] = int(match['revision'])
                for field in ('elo1_before', 'elo2_before'):
                    if field in match:
                        match[field] = float(match[field]) if match[field] else None
                        if match['status'] == 'completed':
                            if match[field] is None or not math.isfinite(match[field]):
                                raise ValueError('Invalid stored Elo')
                        elif match[field] is not None:
                            raise ValueError('Unfinished matches must not have stored Elo')
                if match['target_points'] is None:
                    if match['point_log'] != [] or match['status'] != 'completed' or match['revision'] != 0:
                        raise ValueError('Invalid final-score match metadata')
                    self._validate_match(match['player1'], match['player2'],
                                         match['score1'], match['score2'])
                else:
                    state = live_state(match)
                    status = 'completed' if state['winner'] else 'in_progress'
                    if (match['status'] != status or match['revision'] < len(match['point_log'])
                            or (match['score1'], match['score2']) != (state['score1'], state['score2'])):
                        raise ValueError('Live match does not agree with its point log')
                    for event in match['point_log']:
                        point_time = datetime.fromisoformat(event['timestamp'])
                        if point_time.utcoffset() is None or point_time.utcoffset().total_seconds() != 0:
                            raise ValueError('Point timestamps must be UTC')
                if (bool(match['tournament_id']) != bool(match['fixture_id'])
                        or match['tournament_id'] and match['tournament_id'] not in tournament_ids):
                    raise ValueError('Match references a missing tournament or fixture')
            for tournament in tournaments:
                tournament_state(tournament, matches)
            return players, matches, tournaments
        except (ValueError, csv.Error, UnicodeError) as exc:
            raise StoreError(f'Shared data is malformed: {exc}. No data was changed.') from exc

    @staticmethod
    def _validate_match(a, b, x, y):
        if a == b:
            raise StoreError('Choose two different players.')
        if type(x) is not int or type(y) is not int or min(x, y) < 0:
            raise StoreError('Scores must be nonnegative whole numbers.')
        if x == y:
            raise StoreError('A match must have a winner; tied scores are not allowed.')

    def _write(self, name, fields, rows):
        if name == 'matches.csv':
            rebuild_match_elos(rows)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                             dir=self.directory, prefix='.save-',
                                             delete=False) as handle:
                temporary = Path(handle.name)
                # Shared files must remain writable by colleagues on POSIX mounts too.
                os.chmod(temporary, 0o666)
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    serialized = dict(row)
                    if 'active' in serialized:
                        serialized['active'] = int(serialized['active'])
                    if 'players' in serialized:
                        serialized['players'] = json.dumps(serialized['players'])
                    if 'point_log' in serialized:
                        serialized['point_log'] = json.dumps(serialized['point_log'])
                    writer.writerow(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.directory / name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def snapshot(self):
        with self._locked():
            return self._load()

    def migrate_elo(self):
        """Back up and upgrade an older match table once, under the normal lock."""
        with self._locked():
            _, matches, _ = self._load()
            path = self.directory / 'matches.csv'
            if not path.exists():
                return None
            with path.open(newline='', encoding='utf-8') as handle:
                if next(csv.reader(handle)) == list(self.MATCH_FIELDS):
                    return None
            backup = self.directory / f'matches.pre-elo-{uuid.uuid4().hex}.csv.bak'
            shutil.copy2(path, backup)
            self._write('matches.csv', self.MATCH_FIELDS, matches)
            return backup

    def save_player(self, name, player_id=None):
        name = name.strip()
        if not name:
            raise StoreError('Enter a player name.')
        with self._locked():
            players, matches, tournaments = self._load()
            if any(p['id'] != player_id and p['name'].casefold() == name.casefold()
                   for p in players):
                raise StoreError('That name already exists (including retired players).')
            if player_id is None:
                players.append(dict(id=uuid.uuid4().hex, name=name, active=True))
            else:
                self._player(players, player_id)['name'] = name
            self._write('players.csv', self.PLAYER_FIELDS, players)
            return players, matches, tournaments

    @staticmethod
    def _player(players, player_id):
        for player in players:
            if player['id'] == player_id:
                return player
        raise StoreError('This player no longer exists. Refresh and try again.')

    def set_active(self, player_id, active):
        with self._locked():
            players, matches, tournaments = self._load()
            self._player(players, player_id)['active'] = active
            self._write('players.csv', self.PLAYER_FIELDS, players)
            return players, matches, tournaments

    def register(self, player1, player2, score1, score2):
        self._validate_match(player1, player2, score1, score2)
        with self._locked():
            players, matches, tournaments = self._load()
            for player_id in (player1, player2):
                if not self._player(players, player_id)['active']:
                    raise StoreError('A selected player has been retired. Refresh and try again.')
            matches.append(dict(id=uuid.uuid4().hex,
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                player1=player1, player2=player2,
                                score1=score1, score2=score2, tournament_id='', fixture_id='',
                                target_points=None, point_log=[], status='completed', revision=0))
            self._write('matches.csv', self.MATCH_FIELDS, matches)
            return players, matches, tournaments

    def delete_match(self, match_id):
        with self._locked():
            players, matches, tournaments = self._load()
            match = next((m for m in matches if m['id'] == match_id), None)
            if match and match['tournament_id']:
                latest = next(m for m in reversed(matches)
                              if m['tournament_id'] == match['tournament_id'])
                if match_id != latest['id']:
                    raise StoreError('Undo tournament results in reverse registration order. '
                                     'Use Undo latest result in the Tournament tab first.')
            remaining = [m for m in matches if m['id'] != match_id]
            if len(remaining) == len(matches):
                raise StoreError('This match was already deleted. Refresh and try again.')
            self._write('matches.csv', self.MATCH_FIELDS, remaining)
            return players, remaining, tournaments

    @staticmethod
    def _validate_tournament(system, participants, groups):
        if system not in SYSTEMS:
            raise StoreError('Choose a valid tournament system.')
        if len(participants) < 2 or len(set(participants)) != len(participants):
            raise StoreError('Select at least two distinct players.')
        if system != 'round_robin' and len(participants) % 2:
            raise StoreError('Elimination tournaments require an even number of players. '
                             'Use Round-robin for an odd number.')
        if system == 'group_double':
            if len(participants) < 4:
                raise StoreError('Group-stage double elimination requires at least four players.')
            if type(groups) is not int or not 1 <= groups <= len(participants) // 2:
                raise StoreError('Each group must have at least two players.')
        elif groups != 1:
            raise StoreError('This system does not use multiple groups.')

    def create_tournament(self, name, system, participants, groups=1):
        name = name.strip()
        if not name:
            raise StoreError('Enter a tournament name.')
        self._validate_tournament(system, participants, groups)
        with self._locked():
            players, matches, tournaments = self._load()
            if any(not self._player(players, p)['active'] for p in participants):
                raise StoreError('Only active players can enter a new tournament.')
            seeds = list(participants)
            random.SystemRandom().shuffle(seeds)
            tournaments.append(dict(id=uuid.uuid4().hex, name=name, system=system,
                                    created_at=datetime.now(timezone.utc).isoformat(),
                                    players=seeds, groups=groups))
            self._write('tournaments.csv', self.TOURNAMENT_FIELDS, tournaments)
            return players, matches, tournaments

    def register_tournament_match(self, tournament_id, fixture_id, score1, score2, expected_players):
        with self._locked():
            players, matches, tournaments = self._load()
            tournament = next((t for t in tournaments if t['id'] == tournament_id), None)
            if tournament is None:
                raise StoreError('Tournament not found. Refresh and try again.')
            if any(m.get('tournament_id') == tournament_id and m.get('status') == 'in_progress'
                   for m in matches):
                raise StoreError('Finish the current tournament match before starting another.')
            state = tournament_state(tournament, matches)
            fixture = next((f for f in state['fixtures'] if f['id'] == fixture_id), None)
            if fixture is None or fixture['bye'] or fixture['result'] is not None:
                raise StoreError('This fixture is not available or already has a result. Refresh and try again.')
            if tuple(expected_players) != (fixture['player1'], fixture['player2']):
                raise StoreError('The bracket participants changed. Refresh before entering this result.')
            self._validate_match(fixture['player1'], fixture['player2'], score1, score2)
            matches.append(dict(id=uuid.uuid4().hex,
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                player1=fixture['player1'], player2=fixture['player2'],
                                score1=score1, score2=score2,
                                tournament_id=tournament_id, fixture_id=fixture_id,
                                target_points=None, point_log=[], status='completed', revision=0))
            # The result is the only persisted change. Brackets and completion are
            # derived from it, so a crash cannot leave two tables half-updated.
            self._write('matches.csv', self.MATCH_FIELDS, matches)
            return players, matches, tournaments

    def create_live_tournament_match(self, tournament_id, fixture_id, target_points, expected_players):
        if type(target_points) is not int or target_points < 2:
            raise StoreError('Choose a target of at least 2 points.')
        with self._locked():
            players, matches, tournaments = self._load()
            tournament = next((t for t in tournaments if t['id'] == tournament_id), None)
            if tournament is None:
                raise StoreError('Tournament not found. Refresh and try again.')
            state = tournament_state(tournament, matches)
            fixture = next((f for f in state['fixtures'] if f['id'] == fixture_id), None)
            if (fixture is None or fixture['bye'] or fixture['result'] is not None
                    or fixture['live_match'] is not None):
                raise StoreError('This fixture is not available or already has a result. Refresh and try again.')
            if tuple(expected_players) != (fixture['player1'], fixture['player2']):
                raise StoreError('The bracket participants changed. Refresh before starting the match.')
            matches.append(dict(id=uuid.uuid4().hex, timestamp=datetime.now(timezone.utc).isoformat(),
                                player1=fixture['player1'], player2=fixture['player2'], score1=0, score2=0,
                                tournament_id=tournament_id, fixture_id=fixture_id,
                                target_points=target_points, point_log=[], status='in_progress', revision=0))
            self._write('matches.csv', self.MATCH_FIELDS, matches)
            return players, matches, tournaments

    def create_live_match(self, player1, player2, target_points):
        if player1 == player2:
            raise StoreError('Choose two different players.')
        if type(target_points) is not int or target_points < 2:
            raise StoreError('Choose a target of at least 2 points.')
        with self._locked():
            players, matches, tournaments = self._load()
            if any(not self._player(players, p)['active'] for p in (player1, player2)):
                raise StoreError('Only active players can start a new match.')
            matches.append(dict(id=uuid.uuid4().hex, timestamp=datetime.now(timezone.utc).isoformat(),
                                player1=player1, player2=player2, score1=0, score2=0,
                                tournament_id='', fixture_id='', target_points=target_points,
                                point_log=[], status='in_progress', revision=0))
            self._write('matches.csv', self.MATCH_FIELDS, matches)
            return players, matches, tournaments

    def score_live_match(self, match_id, player_id, expected_revision):
        """Add a point, or undo the last point when player_id is None."""
        with self._locked():
            players, matches, tournaments = self._load()
            match = next((m for m in matches if m['id'] == match_id), None)
            if match is None or match['target_points'] is None:
                raise StoreError('Live match not found. Refresh and try again.')
            if match['revision'] != expected_revision:
                raise StoreError('This match changed on another computer. Refresh before scoring again.')
            if player_id is None:
                if not match['point_log']:
                    raise StoreError('There are no points to undo.')
                match['point_log'].pop()
            else:
                if match['status'] == 'completed':
                    raise StoreError('This match already has a winner.')
                if player_id not in (match['player1'], match['player2']):
                    raise StoreError('The scorer is not playing in this match.')
                match['point_log'].append(dict(player=player_id, timestamp=datetime.now(timezone.utc).isoformat()))
            state = live_state(match)
            match['score1'], match['score2'] = state['score1'], state['score2']
            match['revision'] += 1
            match['status'] = 'completed' if state['winner'] else 'in_progress'
            if state['winner']:
                # Elo follows completion order, not the order live matches began.
                match['timestamp'] = datetime.now(timezone.utc).isoformat()
                matches.remove(match)
                matches.append(match)
            self._write('matches.csv', self.MATCH_FIELDS, matches)
            return players, matches, tournaments
