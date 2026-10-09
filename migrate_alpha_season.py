"""Archive Alpha season results, then clear the active ledgers (run once)."""
import json
import os
import sys
from datetime import datetime, timezone

from ranking import standings
from storage import Store
from tournaments import tournament_state


def migrate(directory='data'):
    store = Store(directory)
    with store._locked():
        path = store.directory / 'alpha-season.json'
        if path.exists():
            raise ValueError('Alpha season is already archived; refusing to reset again.')
        players, matches, tournaments = store._load()
        copa = next(t for t in tournaments if t['name'] == 'Copa Turbo'
                    and t['classification'] == 'major')
        state = tournament_state(copa, matches)
        if not state['complete'] or not state['champion']:
            raise ValueError('Copa Turbo must be complete before archiving.')
        # Keep bracket results, not point logs, match IDs, or rating histories.
        for fixture in state['fixtures']:
            if fixture['result']:
                fixture['result'] = {key: fixture['result'][key]
                                     for key in ('score1', 'score2')}
        rows = [dict(player_id=row['player']['id'], name=row['player']['name'],
                     rank=row['rank'], elo=row['elo'])
                for row in standings(players, matches)]
        archive = dict(name='Alpha season', closed_at=datetime.now(timezone.utc).isoformat(),
                       standings=rows, tournament=copa, state=state,
                       names={p['id']: p['name'] for p in players})
        # Persist the archive before removing any live data. An existing archive
        # prevents a second invocation from erasing a new season's results.
        with path.open('x', encoding='utf-8') as handle:
            json.dump(archive, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        store._write('matches.csv', Store.MATCH_FIELDS, [])
        store._write('tournaments.csv', Store.TOURNAMENT_FIELDS, [])
    return archive


if __name__ == '__main__':
    result = migrate(sys.argv[1] if len(sys.argv) > 1 else 'data')
    print(f"Archived {result['name']}; active matches and tournaments cleared.")
