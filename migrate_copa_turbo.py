"""Run inside the updated app container after deploying tournament format 3."""

from storage import Store
from tournaments import tournament_state


def migrate(directory='data'):
    store = Store(directory)
    with store._locked():
        _, matches, tournaments = store._load()
        tournament = next(t for t in tournaments
                          if t['id'] == '8d03d9bdfc434c48bf265c9ad5b118da')
        if tournament['format_version'] == 3:
            print('Copa Turbo already uses format 3.')
            return
        if tournament['format_version'] != 2:
            raise ValueError('Expected Copa Turbo to use format 2')
        tournament['format_version'] = 3
        # Reject migration if a completed or live fixture conflicts with the new draw.
        tournament_state(tournament, matches)
        path = store.directory / 'tournaments.csv'
        with path.with_name('tournaments.pre-copa-turbo-v3.csv.bak').open('xb') as backup:
            backup.write(path.read_bytes())
        store._write('tournaments.csv', Store.TOURNAMENT_FIELDS, tournaments)
    print('Copa Turbo updated to format 3; all match results preserved.')


if __name__ == '__main__':
    migrate()
