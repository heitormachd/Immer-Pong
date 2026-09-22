"""Small browser interface over the existing CSV ledger."""

from pathlib import Path
import secrets
import hashlib

from flask import Flask, abort, g, redirect, render_template, request, session, url_for, send_from_directory

from werkzeug.exceptions import NotFound
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from badges import BADGES, MAX_UPLOAD, save_badge, valid_badge
from live_scoring import live_state
from ranking import database_stats, match_elo_estimates, match_elo_history, standings
from storage import Store, StoreError
from tournaments import SYSTEMS, tournament_state


def create_app(data_directory=None, url_prefix=""):
    root = Path(__file__).resolve().parent
    directory = Path(data_directory) if data_directory else Path("data")
    directory = (root / directory).resolve()
    prefix = url_prefix.rstrip("/")
    if prefix and (not prefix.startswith("/") or prefix.startswith("//")):
        raise ValueError("URL prefix must start with a single slash")
    app = Flask(__name__)
    # One worker is intentional: serialize this small app's CSV operations.
    # Restarting the service expires open forms; refreshing creates a new token.
    app.config.update(SECRET_KEY=secrets.token_hex(32), SESSION_COOKIE_SAMESITE='Lax',
                      MAX_CONTENT_LENGTH=MAX_UPLOAD + 64 * 1024)
    app.config.update(
        SESSION_COOKIE_NAME='pingpong_' + hashlib.sha256(str(directory).encode()).hexdigest()[:12],
        SESSION_COOKIE_PATH=prefix + '/',
        TEST_SITE=directory.name == 'test_data',
    )
    store = Store(directory)
    store.migrate_elo()
    app.extensions['store'] = store

    def data_version():
        stamps = []
        for name in ('players.csv', 'matches.csv', 'tournaments.csv'):
            try:
                stat = (directory / name).stat()
                stamps.append((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except FileNotFoundError:
                stamps.append(None)
        # A restart also requires fresh forms, since the CSRF secret changes.
        return hashlib.sha256((app.config['SECRET_KEY'] + repr(stamps)).encode()).hexdigest()

    @app.before_request
    def protect_forms():
        if request.method == 'POST':
            token = session.get('csrf', '')
            if not token or not secrets.compare_digest(token.encode(), request.form.get('csrf', '').encode()):
                abort(400, 'This form expired. Refresh the page and try again.')
        session.setdefault('csrf', secrets.token_hex(32))
        if request.method == 'GET' and request.endpoint not in ('static', 'uploaded_badge', 'changes'):
            # Capture before reading data so a concurrent change is never missed.
            g.data_version = data_version()

    @app.get('/changes')
    def changes():
        return {'version': data_version()}

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        return response

    @app.template_global()
    def badge_url(badge):
        return (url_for('static', filename=f'badges/{badge}.svg') if badge in BADGES
                else url_for('uploaded_badge', filename=badge))

    @app.get('/badges/<filename>')
    def uploaded_badge(filename):
        if not valid_badge(filename) or filename in BADGES:
            abort(404)
        return send_from_directory(directory / 'badges', filename, mimetype='image/png')

    def context():
        players, matches, tournaments = store.snapshot()
        return dict(players=sorted(players, key=lambda p: p['name'].casefold()),
                    matches=matches, tournaments=tournaments,
                    names={p['id']: p['name'] for p in players}, systems=SYSTEMS, badges=BADGES,
                    active=sorted((p for p in players if p['active']), key=lambda p: p['name'].casefold()))

    def integer(name):
        try:
            return int(request.form.get(name, ''))
        except ValueError:
            raise StoreError('Scores, targets, and groups must be whole numbers.') from None

    def render(template, tab, **values):
        return render_template(template, tab=tab, **values)

    @app.errorhandler(StoreError)
    def store_error(error):
        return render('error.html', '', message=str(error)), 409

    @app.errorhandler(OSError)
    def storage_error(error):
        app.logger.exception('Storage operation failed')
        return render('error.html', '', message='Could not access the shared data. Refresh and check '
                      'history before retrying: a save may have succeeded.'), 503

    @app.errorhandler(413)
    @app.errorhandler(400)
    @app.errorhandler(404)
    def request_error(error):
        return render('error.html', '', message=error.description), error.code

    @app.route('/', methods=['GET', 'POST'])
    def matches():
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'register':
                store.register(request.form.get('player1'), request.form.get('player2'),
                               integer('score1'), integer('score2'))
            elif action == 'create_live':
                _, rows, _ = store.create_live_match(request.form.get('player1'),
                                                    request.form.get('player2'), integer('target'))
                return redirect(url_for('live', match_id=rows[-1]['id']), 303)
            elif action == 'delete':
                store.delete_match(request.form.get('match_id'))
            else:
                abort(400)
            return redirect(url_for('matches'), 303)
        data = context()
        return render('matches.html', 'matches', **data,
                      elo=match_elo_history(data['players'], data['matches']),
                      tournament_names={t['id']: t['name'] for t in data['tournaments']})

    @app.route('/players', methods=['GET', 'POST'])
    def players():
        if request.method == 'POST':
            action = request.form.get('action')
            if action in ('add', 'rename'):
                player_id = request.form.get('player_id') if action == 'rename' else None
                if action == 'rename' and not player_id:
                    abort(400)
                store.save_player(request.form.get('name', ''), player_id)
            elif action in ('retire', 'restore'):
                store.set_active(request.form.get('player_id'), action == 'restore')
            else:
                abort(400)
            return redirect(url_for('players'), 303)
        return render('players.html', 'players', **context())

    @app.get('/leaderboards')
    def leaderboards():
        data = context()
        return render('leaderboards.html', 'leaderboards', **data,
                      rows=standings(data['players'], data['matches']))

    @app.get('/stats')
    def stats():
        data = context()
        section = request.args.get('section', 'overall')
        if section not in ('overall', 'player'):
            abort(400, 'Unknown stats section.')
        player_id = request.args.get('player', '')
        opponent_id = request.args.get('opponent', '')
        if any(value and value not in data['names'] for value in (player_id, opponent_id)):
            abort(404, 'Player not found.')
        if player_id and player_id == opponent_id:
            opponent_id = ''
        duel_matches = [m for m in data['matches']
                   if m['status'] == 'completed' and player_id and opponent_id
                   and {m['player1'], m['player2']} == {player_id, opponent_id}]
        return render('stats.html', 'stats', **data,
                      stats=database_stats(data['players'], data['matches']), section=section,
                      player_id=player_id, opponent_id=opponent_id,
                      achievements=[t for t in reversed(data['tournaments'])
                                    if player_id and t['classification'] == 'major'
                                    and tournament_state(t, data['matches'])['champion'] == player_id],
                      duel=database_stats(data['players'], duel_matches)['personal']
                      if opponent_id else None, duel_count=len(duel_matches))

    @app.route('/live/<match_id>', methods=['GET', 'POST'])
    def live(match_id):
        if request.method == 'POST':
            action = request.form.get('action')
            if action in ('point', 'undo'):
                player = request.form.get('player_id', '') if action == 'point' else None
                _, rows, _ = store.score_live_match(match_id, player, integer('revision'))
                finished = next((m for m in rows if m['id'] == match_id), None)
                if action == 'point' and finished and finished['status'] == 'completed':
                    if finished['tournament_id']:
                        return redirect(url_for('tournament', tournament_id=finished['tournament_id']), 303)
                    return redirect(url_for('matches'), 303)
            elif action == 'delete':
                store.delete_match(match_id)
                return redirect(url_for('matches'), 303)
            else:
                abort(400)
            return redirect(url_for('live', match_id=match_id), 303)
        data = context()
        match = next((m for m in data['matches'] if m['id'] == match_id and m['target_points']), None)
        if match is None:
            abort(404, 'Live match not found. It may have been deleted.')
        estimates = (match_elo_estimates(data['players'], data['matches'], match)
                     if match['status'] == 'in_progress' else None)
        return render('live.html', 'matches', **data, match=match, state=live_state(match),
                      estimates=estimates)

    @app.route('/tournaments', methods=['GET', 'POST'])
    def tournaments():
        if request.method == 'POST':
            system = request.form.get('system')
            groups = integer('groups') if system == 'group_double' else 1
            badge = request.form.get('badge', 'cup')
            classification = request.form.get('classification', 'minor')
            if classification == 'minor':
                badge = ''
            if classification == 'major' and badge not in BADGES:
                raise StoreError('Choose a default tournament badge.')
            uploaded = request.files.get('image')
            saved = None
            try:
                if classification == 'major' and uploaded and uploaded.filename:
                    try:
                        saved = save_badge(uploaded, directory)
                    except ValueError as exc:
                        raise StoreError(str(exc)) from exc
                    badge = saved
                _, _, rows = store.create_tournament(
                    request.form.get('name', ''), system, request.form.getlist('participants'),
                    groups, classification=classification, badge=badge,
                    target_points=integer('target') if 'target' in request.form else 7,
                    target_stage='all' if system == 'round_robin' else request.form.get('target_stage', 'all'),
                    alternate_target=integer('alternate_target') if system != 'round_robin' and 'alternate_target' in request.form else 11,
                    bo3_stage=('all' if request.form.get('bo3_stage') == 'all' else 'none')
                    if system == 'round_robin' else request.form.get('bo3_stage', 'none'))
            except StoreError:
                if saved:
                    (directory / 'badges' / saved).unlink(missing_ok=True)
                raise
            return redirect(url_for('tournament', tournament_id=rows[-1]['id']), 303)
        data = context()
        return render('tournaments.html', 'tournaments', **data,
                      states={t['id']: tournament_state(t, data['matches']) for t in data['tournaments']})

    @app.route('/tournaments/<tournament_id>', methods=['GET', 'POST'])
    def tournament(tournament_id):
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'start_live':
                _, rows, _ = store.create_live_tournament_match(
                    tournament_id, request.form.get('fixture_id'), None,
                    (request.form.get('player1'), request.form.get('player2')))
                return redirect(url_for('live', match_id=rows[-1]['id']), 303)
            elif action == 'result':
                store.register_tournament_match(tournament_id, request.form.get('fixture_id'),
                                                integer('score1'), integer('score2'),
                                                (request.form.get('player1'), request.form.get('player2')))
            elif action == 'undo':
                # Use the displayed result's ID, never silently undo a newer result.
                data = context()
                match_id = request.form.get('match_id')
                if not any(m['id'] == match_id and m['tournament_id'] == tournament_id
                           and m['status'] == 'completed'
                           for m in data['matches']):
                    raise StoreError('This result no longer exists. Refresh and try again.')
                store.delete_match(match_id)
            else:
                abort(400)
            return redirect(url_for('tournament', tournament_id=tournament_id), 303)
        data = context()
        event = next((t for t in data['tournaments'] if t['id'] == tournament_id), None)
        if event is None:
            abort(404, 'Tournament not found.')
        state = tournament_state(event, data['matches'])
        live_fixture = next((f for f in state['fixtures'] if f['live_match'] is not None), None)
        next_fixture = None if live_fixture else next(
            (f for f in state['fixtures']
             if not f['bye'] and f['result'] is None and f['live_match'] is None), None)
        latest = None if live_fixture else next(
            (m for m in reversed(data['matches'])
             if m['tournament_id'] == tournament_id and m['status'] == 'completed'), None)
        return render('tournament.html', 'tournaments', **data, event=event, latest=latest,
                      state=state, next_fixture=next_fixture, live_fixture=live_fixture)

    if prefix:
        app.wsgi_app = DispatcherMiddleware(NotFound(), {prefix: app.wsgi_app})
    return app
