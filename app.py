# MAGNOLIA-APP-20260723_154413
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from models import db, User, TaggedHorse, SavedSearch, EmailLog, Meeting, Race, Runner, RunnerHistory, ColourOverride, SyncLog, HorseProfile, HorseRun, HorseRunField, Tipster, Tip, TipResult
from sync import sync_todays_races, sync_horse_history, backfill_horse_history, archive_to_runner_history, update_horse_ids_from_runners
from email_service import send_morning_alerts
import json
import os
import hmac
import hashlib
import base64
import time
from datetime import datetime, date

load_dotenv()

ADMIN_EMAIL = 'mark@ukedwards.co.uk'

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-this-in-production')

database_url = os.getenv('DATABASE_URL', 'sqlite:///racing.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

UK_COURSES = {
    'ascot', 'ayr', 'bath', 'beverley', 'brighton', 'carlisle', 'catterick',
    'chelmsford', 'cheltenham', 'chepstow', 'chester', 'doncaster', 'epsom',
    'exeter', 'ffos las', 'goodwood', 'hamilton', 'haydock', 'hereford',
    'huntingdon', 'kempton', 'leicester', 'lingfield', 'ludlow', 'market rasen',
    'musselburgh', 'newbury', 'newcastle', 'newmarket', 'nottingham', 'perth',
    'plumpton', 'pontefract', 'redcar', 'ripon', 'salisbury', 'sandown',
    'sedgefield', 'southwell', 'stratford', 'taunton', 'thirsk', 'uttoxeter',
    'warwick', 'wetherby', 'windsor', 'wolverhampton', 'worcester', 'wincanton',
    'yarmouth', 'york'
}


def is_uk_course(name):
    return (name or '').strip().lower() in UK_COURSES


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


with app.app_context():
    db.create_all()


def sync_and_alert(app):
    sync_todays_races(app)
    send_morning_alerts(app)

def sync_and_settle(app):
    sync_todays_races(app)
    with app.app_context():
        _settle_pending_tips()


scheduler = BackgroundScheduler()
scheduler.add_job(func=lambda: sync_todays_races(app), trigger='interval', minutes=15)
scheduler.add_job(func=lambda: sync_and_alert(app), trigger='cron', hour=5, minute=0)
scheduler.add_job(func=lambda: sync_horse_history(app), trigger='cron', hour=23, minute=0)
scheduler.add_job(func=lambda: archive_to_runner_history(app), trigger='cron', hour=22, minute=30)
scheduler.add_job(func=lambda: update_horse_ids_from_runners(app), trigger='cron', hour=18, minute=0)
scheduler.add_job(func=lambda: sync_and_settle(app), trigger='cron', hour=22, minute=0)
scheduler.start()


def is_admin():
    return current_user.is_authenticated and current_user.email == ADMIN_EMAIL


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', page_id='search', is_admin=is_admin(), can_tipster=is_admin() or getattr(current_user, 'can_see_tipster', False))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')
        if not name or not email or not password:
            flash('All fields are required.', 'error')
            return render_template('register.html')
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('register.html')
        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'error')
            return render_template('register.html')
        user = User(name=name, email=email, created_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(f'Welcome to Magnolia Horses, {name}!', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        user     = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            return redirect(request.args.get('next') or url_for('index'))
        flash('Incorrect email or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ── Email deep-link auth ───────────────────────────────────────────────────────

def _make_email_token(user_id):
    """Generate a 25-hour HMAC token for email deep-links."""
    ts      = int(time.time())
    secret  = (os.getenv('SECRET_KEY') or 'change-this-in-production').encode()
    payload = f'{user_id}|{ts}'
    sig     = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip('=')
    token   = base64.urlsafe_b64encode(payload.encode()).decode().rstrip('=') + '.' + sig_b64
    return token


def _verify_email_token(token, max_age=90000):
    """Verify token and return user_id, or None if invalid/expired."""
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return None
        padding   = '=' * (4 - len(parts[0]) % 4)
        payload   = base64.urlsafe_b64decode(parts[0] + padding).decode()
        user_id, ts = payload.split('|')
        if time.time() - int(ts) > max_age:
            return None
        secret  = (os.getenv('SECRET_KEY') or 'change-this-in-production').encode()
        sig     = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip('=')
        if not hmac.compare_digest(sig_b64, parts[1]):
            return None
        return int(user_id)
    except Exception:
        return None


@app.route('/auth/email')
def email_auth():
    token     = request.args.get('token', '')
    race      = request.args.get('race', '')
    horse     = request.args.get('horse', '')
    user_id   = _verify_email_token(token)
    if not user_id:
        flash('This link has expired. Please log in.', 'error')
        return redirect(url_for('login'))
    user = User.query.get(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('login'))
    login_user(user, remember=False)
    # Redirect to home with race/horse params (token stripped for cleanliness)
    params = []
    if race:  params.append(f'race={race}')
    if horse: params.append(f'horse={horse}')
    dest = '/?' + '&'.join(params) if params else '/'
    return redirect(dest)


@app.route('/my-horses')
@login_required
def my_horses():
    tagged   = TaggedHorse.query.filter_by(user_id=current_user.id).order_by(TaggedHorse.horse_name).all()
    searches = SavedSearch.query.filter_by(user_id=current_user.id).order_by(SavedSearch.name).all()
    today        = date.today().strftime('%Y-%m-%d')
    tagged_names = [t.horse_name.lower() for t in tagged]
    tagged_notes = {t.horse_name.lower(): t.notes for t in tagged}
    running_today = []
    if tagged_names:
        runners = db.session.query(Runner).join(Race).join(Meeting).filter(Meeting.date == today).all()
        for r in runners:
            if r.horse_name.lower() in tagged_names:
                running_today.append({
                    'horse_name': r.horse_name,
                    'meeting':    r.race.meeting.name,
                    'time':       r.race.time,
                    'race_name':  r.race.name,
                    'jockey':     r.jockey or '—',
                    'trainer':    r.trainer or '—',
                    'form':       r.form or '',
                    'colour':     r.colour or '',
                    'notes':      tagged_notes.get(r.horse_name.lower(), ''),
                    'position':   r.position or '',
                })
    searches_display = []
    for s in searches:
        try:
            f = json.loads(s.filters)
        except Exception:
            f = {}
        parts = []
        if f.get('horse'):   parts.append(f"Horse (AI theme): {f['horse']}" if f.get('ai_mode') else f"Horse: {f['horse']}")
        if f.get('jockey'):  parts.append(f"Jockey: {f['jockey']}")
        if f.get('trainer'): parts.append(f"Trainer: {f['trainer']}")
        if f.get('colour'):  parts.append(f"Colour: {f['colour']}")
        if f.get('meeting'): parts.append(f"Meeting: {f['meeting']}")
        if f.get('owner'):   parts.append(f"Owner: {f['owner']}")
        if f.get('uk_only'): parts.append('UK only')
        searches_display.append({
            'id':      s.id,
            'name':    s.name,
            'summary': ', '.join(parts) if parts else 'All runners',
            'alert':   s.alert,
            'filters': s.filters,
        })
    return render_template('my_horses.html', page_id='my_horses', tagged=tagged,
                           running_today=running_today, searches=searches_display,
                           is_admin=is_admin(), can_tipster=is_admin() or getattr(current_user, 'can_see_tipster', False))


@app.route('/account')
@login_required
def account():
    try:
        logs = EmailLog.query.filter_by(user_id=current_user.id)\
            .order_by(EmailLog.id.desc()).limit(10).all()
    except Exception:
        db.create_all()
        logs = []
    return render_template('account.html', page_id='account', logs=logs, is_admin=is_admin(), can_tipster=is_admin() or getattr(current_user, 'can_see_tipster', False))


@app.route('/admin/users')
@login_required
def admin_users():
    if not is_admin():
        return redirect(url_for('index'))
    users = User.query.order_by(User.created_at.desc()).all()
    users_data = []
    for u in users:
        searches = []
        for s in u.searches:
            try:
                f = json.loads(s.filters)
            except Exception:
                f = {}
            parts = []
            if f.get('horse'):   parts.append(f"Horse (AI theme): {f['horse']}" if f.get('ai_mode') else f"Horse: {f['horse']}")
            if f.get('jockey'):  parts.append(f"Jockey: {f['jockey']}")
            if f.get('trainer'): parts.append(f"Trainer: {f['trainer']}")
            if f.get('colour'):  parts.append(f"Colour: {f['colour']}")
            if f.get('meeting'): parts.append(f"Meeting: {f['meeting']}")
            if f.get('uk_only'): parts.append('UK only')
            searches.append({
                'id':      s.id,
                'name':    s.name,
                'summary': ', '.join(parts) if parts else 'All runners',
                'alert':   s.alert,
            })
        users_data.append({
            'id':           u.id,
            'name':         u.name,
            'email':        u.email,
            'created_at':   u.created_at or '—',
            'tagged_count': len(u.tagged),
            'searches':     searches,
            'is_banned':         bool(getattr(u, 'is_banned', False)),
            'banned_at':         getattr(u, 'banned_at', '') or '',
            'is_admin':          u.email == ADMIN_EMAIL,
            'can_see_tipster':   bool(getattr(u, 'can_see_tipster', False)),
        })
    return render_template('admin_users.html', users=users_data, is_admin=True, page_id='admin', can_tipster=True)


# ── Tag API ────────────────────────────────────────────────────────────────────

@app.route('/api/tag', methods=['POST'])
@login_required
def tag_horse():
    data       = request.get_json()
    horse_name = (data or {}).get('horse_name', '').strip()
    notes      = (data or {}).get('notes', '').strip()
    if not horse_name:
        return jsonify({'error': 'horse_name required'}), 400
    existing = TaggedHorse.query.filter_by(user_id=current_user.id, horse_name=horse_name).first()
    if existing:
        existing.notes = notes
        db.session.commit()
        return jsonify({'status': 'updated', 'horse_name': horse_name})
    tag = TaggedHorse(user_id=current_user.id, horse_name=horse_name, notes=notes,
                      tagged_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
    db.session.add(tag)
    db.session.commit()
    return jsonify({'status': 'tagged', 'horse_name': horse_name})


@app.route('/api/untag', methods=['POST'])
@login_required
def untag_horse():
    data       = request.get_json()
    horse_name = (data or {}).get('horse_name', '').strip()
    tag        = TaggedHorse.query.filter_by(user_id=current_user.id, horse_name=horse_name).first()
    if tag:
        db.session.delete(tag)
        db.session.commit()
    return jsonify({'status': 'untagged', 'horse_name': horse_name})


@app.route('/api/my-tags')
@login_required
def my_tags():
    tags = TaggedHorse.query.filter_by(user_id=current_user.id).all()
    return jsonify([{'horse_name': t.horse_name, 'notes': t.notes or ''} for t in tags])


@app.route('/api/tag-notes', methods=['POST'])
@login_required
def update_tag_notes():
    data       = request.get_json()
    horse_name = (data or {}).get('horse_name', '').strip()
    notes      = (data or {}).get('notes', '').strip()
    tag        = TaggedHorse.query.filter_by(user_id=current_user.id, horse_name=horse_name).first()
    if tag:
        tag.notes = notes
        db.session.commit()
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'not found'}), 404


# ── Saved Search API ───────────────────────────────────────────────────────────

@app.route('/api/saved-searches', methods=['GET'])
@login_required
def get_saved_searches():
    searches = SavedSearch.query.filter_by(user_id=current_user.id).order_by(SavedSearch.name).all()
    return jsonify([{'id': s.id, 'name': s.name, 'filters': json.loads(s.filters), 'alert': s.alert}
                    for s in searches])


@app.route('/api/saved-searches', methods=['POST'])
@login_required
def save_search():
    data    = request.get_json()
    name    = (data or {}).get('name', '').strip()
    filters = (data or {}).get('filters', {})
    alert   = (data or {}).get('alert', False)
    if not name:
        return jsonify({'error': 'name required'}), 400
    existing = SavedSearch.query.filter_by(user_id=current_user.id, name=name).first()
    if existing:
        existing.filters = json.dumps(filters)
        existing.alert   = alert
        db.session.commit()
        return jsonify({'status': 'updated', 'id': existing.id})
    s = SavedSearch(user_id=current_user.id, name=name, filters=json.dumps(filters),
                    alert=alert, created_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
    db.session.add(s)
    db.session.commit()
    return jsonify({'status': 'saved', 'id': s.id})


@app.route('/api/saved-searches/<int:search_id>', methods=['PUT'])
@login_required
def update_saved_search(search_id):
    s = SavedSearch.query.filter_by(id=search_id, user_id=current_user.id).first()
    if not s:
        return jsonify({'error': 'not found'}), 404
    data    = request.get_json() or {}
    name    = (data.get('name') or '').strip()
    filters = data.get('filters', {})
    alert   = data.get('alert', False)
    if not name:
        return jsonify({'error': 'name required'}), 400
    # If renaming to a name already used by a different saved search, block it
    clash = SavedSearch.query.filter_by(user_id=current_user.id, name=name).first()
    if clash and clash.id != s.id:
        return jsonify({'error': 'a saved search with that name already exists'}), 400
    s.name    = name
    s.filters = json.dumps(filters)
    s.alert   = alert
    db.session.commit()
    return jsonify({'status': 'updated', 'id': s.id})


@app.route('/api/saved-searches/<int:search_id>', methods=['DELETE'])
@login_required
def delete_saved_search(search_id):
    s = SavedSearch.query.filter_by(id=search_id, user_id=current_user.id).first()
    if s:
        db.session.delete(s)
        db.session.commit()
    return jsonify({'status': 'ok'})


# ── Admin: manage other users' saved searches ──────────────────────────────────

@app.route('/api/admin/saved-searches/<int:search_id>', methods=['GET'])
@login_required
def admin_get_saved_search(search_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    s = SavedSearch.query.get(search_id)
    if not s:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'id': s.id, 'name': s.name, 'filters': json.loads(s.filters),
        'alert': s.alert, 'user_id': s.user_id,
        'user_name': s.user.name, 'user_email': s.user.email
    })


@app.route('/api/admin/saved-searches/<int:search_id>', methods=['PUT'])
@login_required
def admin_update_saved_search(search_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    s = SavedSearch.query.get(search_id)
    if not s:
        return jsonify({'error': 'not found'}), 404
    data    = request.get_json() or {}
    name    = (data.get('name') or '').strip()
    filters = data.get('filters', {})
    alert   = data.get('alert', False)
    if not name:
        return jsonify({'error': 'name required'}), 400
    clash = SavedSearch.query.filter_by(user_id=s.user_id, name=name).first()
    if clash and clash.id != s.id:
        return jsonify({'error': 'a saved search with that name already exists for this user'}), 400
    s.name    = name
    s.filters = json.dumps(filters)
    s.alert   = alert
    db.session.commit()
    return jsonify({'status': 'updated', 'id': s.id})


@app.route('/api/admin/saved-searches/<int:search_id>', methods=['DELETE'])
@login_required
def admin_delete_saved_search(search_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    s = SavedSearch.query.get(search_id)
    if s:
        db.session.delete(s)
        db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/saved-searches/<int:search_id>/alert', methods=['POST'])
@login_required
def toggle_search_alert(search_id):
    s = SavedSearch.query.filter_by(id=search_id, user_id=current_user.id).first()
    if not s:
        return jsonify({'error': 'not found'}), 404
    s.alert = not s.alert
    db.session.commit()
    return jsonify({'status': 'ok', 'alert': s.alert})


# ── Data API ───────────────────────────────────────────────────────────────────

@app.route('/api/check-today')
def check_today():
    today = date.today().strftime('%Y-%m-%d')
    count = db.session.query(Runner).join(Race).join(Meeting).filter(Meeting.date == today).count()
    return jsonify({'count': count, 'date': today})


@app.route('/api/options')
def options():
    jockeys  = db.session.query(Runner.jockey).filter(Runner.jockey != '', Runner.jockey != None)\
        .distinct().order_by(Runner.jockey).all()
    trainers = db.session.query(Runner.trainer).filter(Runner.trainer != '', Runner.trainer != None)\
        .distinct().order_by(Runner.trainer).all()
    owners   = db.session.query(Runner.owner).filter(Runner.owner != '', Runner.owner != None)\
        .distinct().order_by(Runner.owner).all()
    return jsonify({
        'jockeys':  [r[0] for r in jockeys],
        'trainers': [r[0] for r in trainers],
        'owners':   [r[0] for r in owners],
    })


@app.route('/api/search')
def search():
    horse    = request.args.get('horse',    '').strip()
    trainer  = request.args.get('trainer',  '').strip()
    jockey   = request.args.get('jockey',   '').strip()
    colour   = request.args.get('colour',   '').strip()
    meeting  = request.args.get('meeting',  '').strip()
    owner    = request.args.get('owner',    '').strip()
    sort     = request.args.get('sort',     'meeting').strip()
    uk_only  = request.args.get('uk_only',  'true').strip().lower() == 'true'
    ai_names = request.args.get('ai_names', '').strip()

    today = date.today().strftime('%Y-%m-%d')
    query = db.session.query(Runner).join(Race).join(Meeting).filter(Meeting.date == today)

    if trainer: query = query.filter(Runner.trainer == trainer)
    if jockey:  query = query.filter(Runner.jockey == jockey)
    if colour:  query = query.filter(Runner.colour.ilike(f'%{colour}%'))
    if meeting: query = query.filter(Meeting.name.ilike(f'%{meeting}%'))
    if owner:   query = query.filter(Runner.owner == owner)

    runners = query.order_by(Meeting.name, Race.time, Runner.number).all()

    if uk_only:
        runners = [r for r in runners if is_uk_course(r.race.meeting.name)]

    if ai_names:
        name_set = {n.strip().lower() for n in ai_names.replace('%7C', '|').split('|') if n.strip()}
        runners = [r for r in runners if r.horse_name.lower() in name_set]
    elif horse:
        hl = horse.lower()
        runners = [r for r in runners if hl in r.horse_name.lower()]

    tagged_map = {}
    if current_user.is_authenticated:
        for t in TaggedHorse.query.filter_by(user_id=current_user.id).all():
            tagged_map[t.horse_name.lower()] = t.notes or ''

    if sort == 'time':
        return jsonify(sort_by_time(runners, tagged_map))
    else:
        return jsonify(sort_by_meeting(runners, tagged_map))


# ── AI horse name search ───────────────────────────────────────────────────────

_ai_cache = {}

def resolve_ai_theme(term, uk_only=True, all_runners=None):
    """Resolve an AI search theme to a list of matching horse names for today.
    Shared by the interactive AI search endpoint, run-all-searches, and the
    morning email job. Returns a list of horse names (possibly empty) or
    raises an exception on failure (caller decides how to handle).
    """
    term = (term or '').strip()
    if not term:
        return []

    api_key = os.environ.get('ANTHROPIC_API_KEY') or os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not configured on server')

    today     = date.today().strftime('%Y-%m-%d')
    cache_key = f"{today}|{term.lower()}|{'uk' if uk_only else 'all'}"

    if cache_key in _ai_cache:
        return _ai_cache[cache_key]

    if all_runners is None:
        all_runners = db.session.query(Runner).join(Race).join(Meeting)\
            .filter(Meeting.date == today).all()
        if uk_only:
            all_runners = [r for r in all_runners if is_uk_course(r.race.meeting.name)]

    all_names = list({r.horse_name for r in all_runners})
    if not all_names:
        _ai_cache[cache_key] = []
        return []

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    names_text = '\n'.join(sorted(all_names))
    prompt = f"""You are helping search for horse names based on a conceptual theme.

Search theme: "{term}"

Here is the list of today's horse names:
{names_text}

Return ONLY the horse names from the list above that match the theme "{term}" — including names that are thematically related, conceptually linked, or share relevant vocabulary.

For example:
- "music related" would match: Symphony, Encore, Rhythm, Maestro, Jazz, Overture
- "royal/crown themed" would match: Crown Jewel, Palace Guard, Royal Decree, King's Man
- "weather related" would match: Storm Front, Lightning Strike, Fair Wind, Thunder Roll

Return ONLY the matching names, one per line, exactly as they appear in the list. Return nothing else — no explanation, no numbering, no punctuation."""

    message = client.messages.create(
        model='claude-sonnet-4-5',
        max_tokens=1000,
        messages=[{'role': 'user', 'content': prompt}]
    )

    raw     = message.content[0].text.strip()
    matched = [line.strip() for line in raw.split('\n') if line.strip()]

    valid_set = {n.lower(): n for n in all_names}
    matched   = [valid_set[m.lower()] for m in matched if m.lower() in valid_set]

    _ai_cache[cache_key] = matched
    return matched


@app.route('/api/ai-horse-search', methods=['POST'])
def ai_horse_search():
    data    = request.get_json() or {}
    term    = (data.get('term') or '').strip()
    uk_only = data.get('uk_only', True)

    if not term:
        return jsonify({'error': 'term required'}), 400

    today     = date.today().strftime('%Y-%m-%d')
    cache_key = f"{today}|{term.lower()}|{'uk' if uk_only else 'all'}"
    cached    = cache_key in _ai_cache

    try:
        names = resolve_ai_theme(term, uk_only=uk_only)
        return jsonify({'names': names, 'cached': cached})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def runner_to_dict(r, tagged_map):
    return {
        'number':          r.number,
        'name':            r.horse_name,
        'colour':          r.colour,
        'age':             r.age,
        'sex':             r.sex,
        'draw':            r.draw or '',
        'trainer':         r.trainer,
        'jockey':          r.jockey,
        'owner':           r.owner,
        'form':            r.form,
        'weight':          r.weight,
        'or':              r.official_rating,
        'rpr':             r.rpr or '',
        'ts':              r.ts or '',
        'odds':            r.odds,
        'headgear':        r.headgear or '',
        'headgear_run':    r.headgear_run or '',
        'last_run':        r.last_run or '',
        'position':        r.position or '',
        'silk_url':        r.silk_url or '',
        'spotlight':       r.spotlight or '',
        'comment':         r.comment or '',
        'wind_surgery':    r.wind_surgery or '',
        'trainer_14_days': r.trainer_14_days or '',
        'horse_id':        r.horse_id or '',
        'sp':              r.sp or '',
        'tagged':          r.horse_name.lower() in tagged_map,
        'notes':           tagged_map.get(r.horse_name.lower(), ''),
    }


def build_race_obj(r_data, tagged_map):
    race          = r_data['race']
    runners       = r_data['runners']
    is_result     = (race.race_status or '').lower() == 'result' or                     any(r.position for r in runners)
    total_runners = len(race.runners)

    if is_result:
        def pos_key(r):
            try: return int(r.position or 0)
            except (ValueError, TypeError): return 999
        runners = sorted(runners, key=pos_key)
    else:
        def num_key(r):
            try: return int(r.number or 0)
            except (ValueError, TypeError): return 0
        runners = sorted(runners, key=num_key)

    return {
        'time':           race.time,
        'name':           race.name,
        'distance':       race.distance,
        'class':          race.race_class,
        'is_result':      is_result,
        'going_detailed': race.going_detailed or '',
        'weather':        race.weather or '',
        'total_runners':  total_runners,
        'runners':        [runner_to_dict(r, tagged_map) for r in runners]
    }


def sort_by_meeting(runners, tagged_map):
    grouped = {}
    for r in runners:
        m_key = r.race.meeting.name
        r_key = r.race.id
        if m_key not in grouped:
            grouped[m_key] = {'meeting': r.race.meeting, 'races': {}}
        if r_key not in grouped[m_key]['races']:
            grouped[m_key]['races'][r_key] = {'race': r.race, 'runners': []}
        grouped[m_key]['races'][r_key]['runners'].append(r)
    result = []
    for m_name, m_data in grouped.items():
        meeting_obj = {'meeting': m_data['meeting'].name, 'date': m_data['meeting'].date, 'races': []}
        for r_id, r_data in m_data['races'].items():
            meeting_obj['races'].append(build_race_obj(r_data, tagged_map))
        meeting_obj['races'].sort(key=lambda x: x['time'])
        result.append(meeting_obj)
    return result


def sort_by_time(runners, tagged_map):
    races = {}
    for r in runners:
        r_key = r.race.id
        if r_key not in races:
            races[r_key] = {'race': r.race, 'runners': []}
        races[r_key]['runners'].append(r)
    time_groups = {}
    for r_key, r_data in races.items():
        t = r_data['race'].time
        if t not in time_groups:
            time_groups[t] = []
        time_groups[t].append(r_data)
    result = []
    for time_slot in sorted(time_groups.keys()):
        for r_data in time_groups[time_slot]:
            race = r_data['race']
            result.append({'meeting': race.meeting.name, 'date': race.meeting.date,
                           'races': [build_race_obj(r_data, tagged_map)]})
    return result


@app.route('/api/run-all-searches')
@login_required
def run_all_searches():
    searches = SavedSearch.query.filter_by(user_id=current_user.id).all()
    if not searches:
        return jsonify([])
    today       = date.today().strftime('%Y-%m-%d')
    all_runners = db.session.query(Runner).join(Race).join(Meeting).filter(Meeting.date == today).all()
    tagged_map  = {t.horse_name.lower(): t.notes or ''
                   for t in TaggedHorse.query.filter_by(user_id=current_user.id).all()}
    matched_ids  = {}  # id -> runner
    match_reasons = {}  # id -> [reason, ...]

    # Favourites
    tagged_names = set(tagged_map.keys())
    for r in all_runners:
        if r.horse_name.lower() in tagged_names:
            matched_ids[r.id] = r
            match_reasons.setdefault(r.id, []).append('Favourite')

    for saved in searches:
        try:
            f = json.loads(saved.filters)
        except Exception:
            continue

        ai_names_set = None
        hf = (f.get('horse') or '').strip()
        if hf and f.get('ai_mode'):
            try:
                uk_only_pref = f.get('uk_only', True)
                pool = all_runners
                if uk_only_pref:
                    pool = [r for r in pool if is_uk_course(r.race.meeting.name)]
                resolved = resolve_ai_theme(hf, uk_only=uk_only_pref, all_runners=pool)
                ai_names_set = {n.lower() for n in resolved}
            except Exception:
                continue

        for r in all_runners:
            if f.get('uk_only') and not is_uk_course(r.race.meeting.name): continue
            if f.get('colour')  and f['colour'].lower()  not in (r.colour or '').lower():  continue
            if f.get('meeting') and f['meeting'].lower() not in r.race.meeting.name.lower(): continue
            if f.get('jockey')  and f['jockey'].lower()  != (r.jockey or '').lower():       continue
            if f.get('trainer') and f['trainer'].lower() != (r.trainer or '').lower():       continue
            if f.get('owner')   and f['owner'].lower()   != (r.owner or '').lower():         continue
            if hf:
                if ai_names_set is not None:
                    if r.horse_name.lower() not in ai_names_set:
                        continue
                elif hf.lower() not in r.horse_name.lower():
                    continue
            matched_ids[r.id] = r
            match_reasons.setdefault(r.id, []).append('Search: ' + saved.name)

    matched = list(matched_ids.values())
    if not matched:
        return jsonify([])

    # Attach reasons to runner dicts via tagged_map augmented with reason
    # We pass reasons through by temporarily enriching tagged_map notes
    result = sort_by_meeting(matched, tagged_map)
    # Walk result and inject match_reason per runner
    for meeting in result:
        for race in meeting.get('races', []):
            for runner in race.get('runners', []):
                rid = next((r.id for r in matched if r.horse_name == runner['name']), None)
                runner['match_reason'] = ' & '.join(match_reasons.get(rid, [])) if rid else ''
    return jsonify(result)


@app.route('/api/sync', methods=['POST'])
@login_required
def manual_sync():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    sync_todays_races(app)
    _settle_pending_tips()
    update_horse_ids_from_runners(app)
    return jsonify({'status': 'ok'})


@app.route('/api/sync-log')
@login_required
def sync_log():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    logs = SyncLog.query.order_by(SyncLog.id.desc()).limit(100).all()
    return jsonify([{'id': l.id, 'created_at': l.created_at, 'level': l.level, 'message': l.message} for l in logs])


@app.route('/api/email-log')
@login_required
def email_log():
    try:
        logs = EmailLog.query.filter_by(user_id=current_user.id)\
            .order_by(EmailLog.id.desc()).limit(10).all()
    except Exception:
        db.create_all()
        logs = []
    return jsonify([{'id': l.id, 'subject': l.subject, 'status': l.status, 'sent_at': l.sent_at}
                    for l in logs])


@app.route('/api/email-log/<int:log_id>')
@login_required
def email_log_detail(log_id):
    log = EmailLog.query.filter_by(id=log_id, user_id=current_user.id).first()
    if not log:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'id': log.id, 'subject': log.subject, 'status': log.status,
                    'sent_at': log.sent_at, 'html_body': log.html_body})


@app.route('/api/send-test-email', methods=['POST'])
@login_required
def send_test_email():
    from email_service import send_morning_alerts_for_user
    result = send_morning_alerts_for_user(current_user.id, app)
    return jsonify(result)




@app.route('/tipster')
@login_required
def tipster_page():
    if not is_admin() and not getattr(current_user, 'can_see_tipster', False):
        flash('You do not have access to the tipster section.', 'error')
        return redirect(url_for('index'))
    return render_template('tipster.html', is_admin=is_admin(), page_id='tipster', can_tipster=True)


@app.route('/admin/tipster')
@login_required
def admin_tipster():
    if not is_admin():
        return redirect(url_for('index'))
    return render_template('admin_tipster.html', is_admin=True, page_id='admin')

@app.route('/admin/colours')
def admin_colours():
    return render_template('admin_colours.html', is_admin=True, page_id='admin')


@app.route('/api/colours/runners')
def colour_runners():
    search    = request.args.get('q', '').strip().lower()
    query     = db.session.query(Runner).join(Race).join(Meeting)
    if search: query = query.filter(Runner.horse_name.ilike(f'%{search}%'))
    runners   = query.order_by(Runner.horse_name).limit(100).all()
    overrides = {o.horse_name.lower(): o.colour for o in ColourOverride.query.all()}
    return jsonify([{'horse_name': r.horse_name, 'colour': r.colour,
                     'has_override': r.horse_name.lower() in overrides,
                     'meeting': r.race.meeting.name, 'race': r.race.name} for r in runners])


@app.route('/api/colours/override', methods=['POST'])
def set_colour_override():
    data       = request.get_json()
    horse_name = data.get('horse_name', '').strip()
    colour     = data.get('colour', '').strip()
    if not horse_name or not colour:
        return jsonify({'error': 'required'}), 400
    override = ColourOverride.query.filter_by(horse_name=horse_name).first()
    if override:
        override.colour     = colour
        override.updated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    else:
        db.session.add(ColourOverride(horse_name=horse_name, colour=colour,
                                      updated_at=datetime.now().strftime('%Y-%m-%d %H:%M')))
    for r in Runner.query.filter(Runner.horse_name.ilike(horse_name)).all():
        r.colour = colour
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/colours/overrides')
def list_overrides():
    return jsonify([{'horse_name': o.horse_name, 'colour': o.colour, 'updated_at': o.updated_at}
                    for o in ColourOverride.query.order_by(ColourOverride.horse_name).all()])


@app.route('/api/colours/override/<horse_name>', methods=['DELETE'])
def delete_override(horse_name):
    override = ColourOverride.query.filter_by(horse_name=horse_name).first()
    if override:
        db.session.delete(override)
        db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/debug-results')
@login_required
def debug_results():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    import requests as req
    auth = (os.getenv('RACING_API_USER'), os.getenv('RACING_API_KEY'))
    BASE = 'https://api.theracingapi.com/v1'
    res = req.get(f'{BASE}/results/today', auth=auth).json()
    result_keys = {}
    for race in res.get('results', []):
        course  = (race.get('course') or '').strip().lower()
        off     = (race.get('off') or '').strip()
        key     = f'{course}_{off}'
        runners = race.get('runners', [])
        result_keys[key] = {
            'sample_horse': (runners[0].get('horse') or '') if runners else '',
            'sample_pos':   (runners[0].get('position') or '') if runners else '',
            'sample_sp':    (runners[0].get('sp_dec') or '') if runners else '',
        }
    rc = req.get(f'{BASE}/racecards/basic', auth=auth).json()
    racecard_keys = {}
    for racecard in rc.get('racecards', []):
        if (racecard.get('race_status') or '').lower() != 'result':
            continue
        course   = (racecard.get('course') or '').strip().lower()
        off_time = (racecard.get('off_time') or '').strip()
        key      = f'{course}_{off_time}'
        racecard_keys[key] = {
            'match_found': key in result_keys,
            'result_data': result_keys.get(key, {})
        }
    return jsonify({
        'result_keys':   result_keys,
        'racecard_keys': racecard_keys,
        'summary': {
            'result_races':       len(result_keys),
            'finished_racecards': len(racecard_keys),
            'matched':   sum(1 for v in racecard_keys.values() if v['match_found']),
            'unmatched': sum(1 for v in racecard_keys.values() if not v['match_found']),
        }
    })



# ── Manual tip result entry ────────────────────────────────────────────────────

@app.route('/api/admin/tip-result/<int:tip_id>', methods=['POST'])
@login_required
def admin_set_tip_result(tip_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    from tip_parser import settle_tip as _settle
    tip = Tip.query.get(tip_id)
    if not tip:
        return jsonify({'error': 'not found'}), 404
    data     = request.get_json() or {}
    position = str(data.get('position', '')).strip()
    sp_str   = str(data.get('sp', '')).strip()
    sp_dec   = 0.0
    try:
        # Parse fractional SP e.g. "8/1" -> 9.0
        if '/' in sp_str:
            parts = sp_str.split('/')
            sp_dec = round(float(parts[0]) / float(parts[1]) + 1, 4)
        else:
            sp_dec = float(sp_str)
    except Exception:
        pass
    result = _settle(tip, position, sp_dec)
    tr = tip.result
    if not tr:
        tr = TipResult(tip_id=tip.id)
        db.session.add(tr)
    tr.position    = position
    tr.sp          = sp_str
    tr.sp_dec      = sp_dec
    tr.result_type = result['result_type']
    tr.win_pts     = result['win_pts']
    tr.place_pts   = result['place_pts']
    tr.total_pts   = result['total_pts']
    tr.settled_at  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tip.settled    = True
    db.session.commit()
    return jsonify({'status': 'ok', 'result_type': result['result_type'],
                    'total_pts': result['total_pts']})


@app.route('/api/admin/archive-runners', methods=['POST'])
@login_required
def admin_archive_runners():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    count = archive_to_runner_history(app)
    return jsonify({'status': 'ok', 'archived': count})



@app.route('/api/admin/backfill-tof-json', methods=['POST'])
@login_required
def admin_backfill_tof_json():
    """Accept a Telegram JSON export file and parse all tips from it."""
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403

    from tip_parser import parse_message as _parse
    import re as _re
    from datetime import timedelta as _td

    def _get_text(m):
        text = m.get('text', '')
        if isinstance(text, list):
            return ''.join(t.get('text','') if isinstance(t,dict) else str(t) for t in text)
        return text.strip()

    def _race_date(dt_str):
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.hour >= 12:
                return (dt + _td(days=1)).strftime('%Y-%m-%d')
            return dt.strftime('%Y-%m-%d')
        except Exception:
            return dt_str[:10] if dt_str else ''

    # Accept JSON body or file upload
    if request.content_type and 'multipart' in request.content_type:
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'no file'}), 400
        raw = f.read().decode('utf-8', errors='replace')
    else:
        raw = request.get_data(as_text=True)

    # Parse the Telegram export (array of message objects)
    try:
        if raw.strip().startswith('['):
            messages = json.loads(raw)
        else:
            messages = json.loads('[' + raw + ']')
    except Exception as e:
        return jsonify({'error': f'JSON parse error: {e}'}), 400

    tipster  = _get_or_create_tipster('Turn Of Foot')
    created  = 0
    skipped  = 0

    for m in messages:
        text = _get_text(m)
        if not text:
            continue
        msg_dt       = m.get('date', '')
        msg_id       = m.get('id', 0)
        race_date    = _race_date(msg_dt)
        msg_datetime = msg_dt.replace('T', ' ') if msg_dt else ''

        if msg_id and Tip.query.filter_by(telegram_msg_id=msg_id).first():
            skipped += 1
            continue

        tips = _parse(text)
        for t in tips:
            if not t.get('horse_name'):
                continue
            tip = Tip(
                tipster_id       = tipster.id,
                horse_name       = t['horse_name'],
                tip_date         = msg_datetime[:10],
                tip_datetime     = msg_datetime,
                course           = t.get('course', ''),
                race_time        = t.get('race_time', ''),
                race_date        = race_date,
                bet_type         = t.get('bet_type', 'ew'),
                stake_pts        = t.get('stake_pts', 0.5),
                odds             = t.get('odds', ''),
                odds_dec         = t.get('odds_dec', 0.0),
                each_way_places  = t.get('each_way_places', 4),
                each_way_fraction= t.get('each_way_fraction', 5),
                reasoning        = t.get('reasoning', ''),
                raw_message      = text,
                telegram_msg_id  = msg_id,
                uncertain        = t.get('uncertain', False),
                created_at       = datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            )
            db.session.add(tip)
            created += 1

        if created % 100 == 0 and created > 0:
            db.session.flush()

    db.session.commit()
    _settle_pending_tips()
    return jsonify({'status': 'ok', 'created': created, 'skipped': skipped,
                    'messages_processed': len(messages)})



@app.route('/api/admin/settle-from-results-json', methods=['POST'])
@login_required
def settle_from_results_json():
    """Accept a results JSON file (list of runner rows) and settle matching tips."""
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403

    import re as _re
    from tip_parser import settle_tip as _settle

    def _strip(name):
        return _re.sub(r'\s*\([A-Z]+\)\s*$', '', name or '').strip().lower()

    def _frac_to_dec(sp):
        try:
            if '/' in str(sp):
                p = str(sp).split('/')
                return round(float(p[0]) / float(p[1]) + 1, 4)
            return float(sp)
        except Exception:
            return 0.0

    # Accept file upload or raw JSON body
    if request.content_type and 'multipart' in request.content_type:
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'no file'}), 400
        raw = f.read().decode('utf-8', errors='replace')
    else:
        raw = request.get_data(as_text=True)

    try:
        results = json.loads(raw)
    except Exception as e:
        return jsonify({'error': f'JSON parse error: {e}'}), 400

    # Build lookup: (date, stripped_horse_name) -> result row
    lookup = {}
    for r in results:
        key = (r.get('date', ''), _strip(r.get('horse', '')))
        lookup[key] = r

    # Also build horse-name -> all rows lookup for fuzzy date matching
    horse_rows = {}
    for r in results:
        h = _strip(r.get('horse', ''))
        if h not in horse_rows:
            horse_rows[h] = []
        horse_rows[h].append(r)

    # Find unsettled tips and match
    unsettled = Tip.query.filter_by(settled=False).all()
    settled_count = 0
    no_match = 0

    for tip in unsettled:
        h = _strip(tip.horse_name)
        race_date = tip.race_date or ''

        # Try exact date first, then ±1 day to handle tipster timing variations
        row = None
        from datetime import timedelta as _td2, datetime as _dt2
        for offset in [0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5]:
            try:
                check_date = (_dt2.strptime(race_date, '%Y-%m-%d') + _td2(days=offset)).strftime('%Y-%m-%d')
            except Exception:
                continue
            key = (check_date, h)
            if key in lookup:
                row = lookup[key]
                # If date was wrong, correct it in the DB
                if offset != 0:
                    tip.race_date = check_date
                break

        if not row:
            candidates = horse_rows.get(h, [])
            if len(candidates) == 1:
                # Only one result for this horse - use it
                row = candidates[0]
                tip.race_date = row['date']
            elif len(candidates) > 1 and tip.race_time:
                # Multiple results - try to match by time (convert tip 1:20 to 13:20)
                tip_time = tip.race_time.replace('.', ':')
                try:
                    th, tm = tip_time.split(':')
                    th = int(th)
                    tip_time_24 = f"{th+12}:{tm}" if th < 12 else tip_time
                except Exception:
                    tip_time_24 = tip_time
                for c in candidates:
                    if c.get('off', '') in (tip_time, tip_time_24):
                        row = c
                        tip.race_date = c['date']
                        break
            if not row:
                no_match += 1
                continue

        pos     = str(row.get('pos', '') or '').strip()
        sp_str  = str(row.get('sp', '') or '').strip()
        ran     = int(row.get('ran', 0) or 0)
        sp_dec  = _frac_to_dec(sp_str)

        # Apply place rule if places not specified
        if tip.each_way_places == 0 and tip.bet_type == 'ew':
            if ran <= 4:   places = 0
            elif ran <= 7: places = 2
            elif ran <= 11: places = 3
            elif ran <= 15: places = 4
            elif ran <= 19: places = 4
            else:           places = 5
            tip.each_way_places = places

        # Handle NR
        if pos.upper() in ('NR', 'W/O', 'SCR'):
            tr = tip.result
            if not tr:
                tr = TipResult(tip_id=tip.id)
                db.session.add(tr)
            tr.position    = pos
            tr.sp          = sp_str
            tr.sp_dec      = 0.0
            tr.result_type = 'nr'
            tr.win_pts     = 0.0
            tr.place_pts   = 0.0
            tr.total_pts   = 0.0
            tr.settled_at  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            tip.settled    = True
            settled_count += 1
            continue

        result = _settle(tip, pos, sp_dec)
        tr = tip.result
        if not tr:
            tr = TipResult(tip_id=tip.id)
            db.session.add(tr)
        tr.position    = pos
        tr.sp          = sp_str
        tr.sp_dec      = sp_dec
        tr.result_type = result['result_type']
        tr.win_pts     = result['win_pts']
        tr.place_pts   = result['place_pts']
        tr.total_pts   = result['total_pts']
        tr.settled_at  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        tip.settled    = True
        settled_count += 1

    db.session.commit()

    # Populate RunnerHistory via bulk INSERT in background thread
    import threading as _threading
    def _populate_rh(results_data):
        with app.app_context():
            try:
                from sqlalchemy import text as _text
                _sql = (
                    'INSERT INTO runner_history '
                    '(race_date,course,race_time,race_name,race_class,distance,going,'
                    'horse_id,horse_name,number,draw,age,sex,trainer,jockey,owner,'
                    'form,weight,official_rating,rpr,ts,odds,sp,headgear,'
                    'last_run,position,silk_url,wind_surgery,trainer_14_days) '
                    'VALUES '
                    '(:race_date,:course,:race_time,:race_name,:race_class,:distance,:going,'
                    ':horse_id,:horse_name,:number,:draw,:age,:sex,:trainer,:jockey,:owner,'
                    ':form,:weight,:official_rating,:rpr,:ts,:odds,:sp,:headgear,'
                    ':last_run,:position,:silk_url,:wind_surgery,:trainer_14_days) '
                    'ON CONFLICT (horse_name,race_date,course,race_time) DO UPDATE SET '
                    'position=EXCLUDED.position, sp=EXCLUDED.sp'
                )
                rows = []
                for r in results_data:
                    if not r.get('date') or not r.get('horse'):
                        continue
                    rows.append({
                        'race_date': r.get('date',''), 'course': r.get('course',''),
                        'race_time': r.get('off',''), 'race_name': r.get('race_name',''),
                        'race_class': r.get('class',''), 'distance': r.get('dist',''),
                        'going': r.get('going',''), 'horse_id': '',
                        'horse_name': r.get('horse',''),
                        'number': str(r.get('num','')), 'draw': str(r.get('draw','')),
                        'age': str(r.get('age','')), 'sex': str(r.get('sex','')),
                        'trainer': r.get('trainer',''), 'jockey': r.get('jockey',''),
                        'owner': r.get('owner',''), 'form': '',
                        'weight': str(r.get('wgt','')),
                        'official_rating': str(r.get('or','')),
                        'rpr': str(r.get('rpr','')), 'ts': str(r.get('ts','')),
                        'odds': '', 'sp': str(r.get('sp','')),
                        'headgear': str(r.get('hg','')), 'last_run': '',
                        'position': str(r.get('pos','')),
                        'silk_url': '', 'wind_surgery': '', 'trainer_14_days': '',
                    })
                for i in range(0, len(rows), 1000):
                    db.session.execute(_text(_sql), rows[i:i+1000])
                    db.session.commit()
                print(f"[RunnerHistory] bulk upserted {len(rows)} rows")
            except Exception as e:
                db.session.rollback()
                print(f"[RunnerHistory] error: {e}")

    t = _threading.Thread(target=_populate_rh, args=(results,))
    t.daemon = True
    t.start()

    return jsonify({'status': 'ok', 'settled': settled_count,
                    'no_match': no_match, 'total_unsettled': len(unsettled),
                    'runner_history': 'populating in background'})



@app.route('/api/admin/cleanup-tips', methods=['POST'])
@login_required
def admin_cleanup_tips():
    """Remove pre-2026 tips and duplicates."""
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403

    from sqlalchemy import text as _text

    # Delete tip_results for pre-2026 tips
    db.session.execute(_text(
        "DELETE FROM tip_result WHERE tip_id IN "
        "(SELECT id FROM tip WHERE tip_date < '2026-01-01')"
    ))

    # Delete pre-2026 tips
    r1 = db.session.execute(_text(
        "DELETE FROM tip WHERE tip_date < '2026-01-01'"
    ))

    # Delete tip_results for duplicate tips (keep settled preferring lower id)
    db.session.execute(_text(
        "DELETE FROM tip_result WHERE tip_id IN ("
        "  SELECT t.id FROM tip t WHERE EXISTS ("
        "    SELECT 1 FROM tip t2"
        "    WHERE t2.horse_name = t.horse_name"
        "      AND t2.race_date = t.race_date"
        "      AND t2.course = t.course"
        "      AND t2.race_time = t.race_time"
        "      AND (t2.settled > t.settled"
        "           OR (t2.settled = t.settled AND t2.id < t.id))"
        "  )"
        ")"
    ))

    # Delete duplicate tips
    r2 = db.session.execute(_text(
        "DELETE FROM tip WHERE EXISTS ("
        "  SELECT 1 FROM tip t2"
        "  WHERE t2.horse_name = tip.horse_name"
        "    AND t2.race_date = tip.race_date"
        "    AND t2.course = tip.course"
        "    AND t2.race_time = tip.race_time"
        "    AND (t2.settled > tip.settled"
        "         OR (t2.settled = tip.settled AND t2.id < tip.id))"
        ")"
    ))

    db.session.commit()

    total = db.session.execute(_text("SELECT COUNT(*) FROM tip")).scalar()
    settled = db.session.execute(_text("SELECT COUNT(*) FROM tip WHERE settled = true")).scalar()

    return jsonify({
        'status': 'ok',
        'pre_2026_deleted': r1.rowcount,
        'duplicates_deleted': r2.rowcount,
        'total_remaining': total,
        'settled': settled,
        'unsettled': total - settled,
    })



@app.route('/api/admin/tip/<int:tip_id>', methods=['DELETE'])
@login_required
def admin_delete_tip(tip_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    tip = Tip.query.get(tip_id)
    if not tip:
        return jsonify({'error': 'not found'}), 404
    if tip.result:
        db.session.delete(tip.result)
    db.session.delete(tip)
    db.session.commit()
    return jsonify({'status': 'ok'})



@app.route('/api/admin/user/<int:user_id>/ban', methods=['POST'])
@login_required
def admin_ban_user(user_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'not found'}), 404
    if user.email == ADMIN_EMAIL:
        return jsonify({'error': 'Cannot ban admin'}), 400
    user.is_banned = True
    user.banned_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/admin/user/<int:user_id>/unban', methods=['POST'])
@login_required
def admin_unban_user(user_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'not found'}), 404
    user.is_banned = False
    user.banned_at = None
    db.session.commit()
    return jsonify({'status': 'ok'})



@app.route('/api/admin/tip-edit/<int:tip_id>', methods=['POST'])
@login_required
def admin_edit_tip(tip_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    from tip_parser import settle_tip as _settle, fractional_to_decimal
    tip = Tip.query.get(tip_id)
    if not tip:
        return jsonify({'error': 'not found'}), 404
    data  = request.get_json() or {}
    field = data.get('field', '')
    value = str(data.get('value', '')).strip()

    if field == 'race_date':
        tip.race_date = value
    elif field == 'odds':
        tip.odds = value
        try:
            if '/' in value:
                p = value.split('/')
                tip.odds_dec = round(float(p[0])/float(p[1])+1, 4)
            else:
                tip.odds_dec = float(value)
        except Exception:
            pass
    elif field in ('position', 'sp'):
        tr = tip.result
        if not tr:
            tr = TipResult(tip_id=tip.id)
            db.session.add(tr)
            db.session.flush()
        if field == 'position':
            tr.position = value
        elif field == 'sp':
            tr.sp = value
            try:
                if '/' in value:
                    p = value.split('/')
                    tr.sp_dec = round(float(p[0])/float(p[1])+1, 4)
                else:
                    tr.sp_dec = float(value)
            except Exception:
                pass
        # Recalculate P&L if we have both position and SP
        if tr.position and tr.sp_dec and tr.sp_dec > 1.0:
            result = _settle(tip, tr.position, tr.sp_dec)
            tr.result_type = result['result_type']
            tr.win_pts     = result['win_pts']
            tr.place_pts   = result['place_pts']
            tr.total_pts   = result['total_pts']
            tr.settled_at  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            tip.settled    = True
    else:
        return jsonify({'error': 'unknown field'}), 400

    db.session.commit()
    return jsonify({'status': 'ok'})



@app.route('/api/admin/user/<int:user_id>/tipster', methods=['POST'])
@login_required
def admin_toggle_tipster(user_id):
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'not found'}), 404
    user.can_see_tipster = not bool(getattr(user, 'can_see_tipster', False))
    db.session.commit()
    return jsonify({'status': 'ok', 'can_see_tipster': user.can_see_tipster})



@app.route('/api/today-tips')
@login_required
def get_today_tips():
    """Return today's tipped horses for badge display on search page."""
    if not is_admin() and not getattr(current_user, 'can_see_tipster', False):
        return jsonify({'tips': {}})
    today = date.today().strftime('%Y-%m-%d')
    tips = Tip.query.filter_by(race_date=today).all()
    result = {}
    for t in tips:
        key = t.horse_name.lower().strip()
        if key not in result:
            result[key] = []
        result[key].append({
            'tipster': t.tipster.name if t.tipster else 'TOF',
            'odds': t.odds,
            'bet_type': t.bet_type,
            'stake_pts': t.stake_pts,
            'course': t.course,
            'race_time': t.race_time,
        })
    return jsonify({'tips': result, 'date': today})



# ── Horse history API ──────────────────────────────────────────────────────────

@app.route('/api/horse-history/<horse_id>')
def horse_history(horse_id):
    runs = HorseRun.query.filter_by(horse_id=horse_id)        .order_by(HorseRun.date.desc()).all()
    if not runs:
        return jsonify([])
    result = []
    for run in runs:
        field = [{
            'horse_id':   f.horse_id,
            'horse_name': f.horse_name,
            'position':   f.position,
            'sp':         f.sp,
            'sp_dec':     f.sp_dec,
            'jockey':     f.jockey,
            'trainer':    f.trainer,
            'weight':     f.weight,
            'btn':        f.btn,
            'or':         f.official_rating,
            'silk_url':   f.silk_url,
        } for f in sorted(run.field, key=lambda x: int(x.position) if x.position.isdigit() else 999)]
        result.append({
            'race_id':    run.race_id,
            'date':       run.date,
            'course':     run.course,
            'race_name':  run.race_name,
            'type':       run.race_type,
            'class':      run.race_class,
            'pattern':    run.pattern,
            'dist':       run.dist,
            'going':      run.going,
            'surface':    run.surface,
            'position':   run.position,
            'sp':         run.sp,
            'sp_dec':     run.sp_dec,
            'jockey':     run.jockey,
            'trainer':    run.trainer,
            'weight':     run.weight,
            'btn':        run.btn,
            'ovr_btn':    run.ovr_btn,
            'or':         run.official_rating,
            'prize':      run.prize,
            'comment':    run.comment,
            'field':      field,
        })
    return jsonify(result)


@app.route('/api/admin/backfill-history', methods=['POST'])
@login_required
def admin_backfill_history():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    import threading
    t = threading.Thread(target=backfill_horse_history, args=(app,))
    t.daemon = True
    t.start()
    return jsonify({'status': 'started', 'message': 'Backfill running in background — check sync log'})


@app.route('/api/admin/sync-history', methods=['POST'])
@login_required
def admin_sync_history():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    import threading
    t = threading.Thread(target=sync_horse_history, args=(app,))
    t.daemon = True
    t.start()
    return jsonify({'status': 'started', 'message': 'History sync running in background — check sync log'})



# ── Tipster webhook ────────────────────────────────────────────────────────────

TIPSTER_WEBHOOK_SECRET = os.getenv('TIPSTER_WEBHOOK_SECRET', '')

def _get_or_create_tipster(name):
    t = Tipster.query.filter_by(name=name).first()
    if not t:
        t = Tipster(name=name, created_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        db.session.add(t)
        db.session.flush()
    return t


def _settle_pending_tips():
    """Try to settle unsettled tips from Runner (today) and RunnerHistory (historical)."""
    from tip_parser import settle_tip as _settle
    import re as _re

    def _strip(name):
        return _re.sub(r'\s*\([A-Z]+\)\s*$', '', name or '').strip().lower()

    unsettled = Tip.query.filter_by(settled=False).all()
    settled_count = 0

    for tip in unsettled:
        tip_name    = _strip(tip.horse_name)
        race_date   = tip.race_date or ''
        course      = _strip(tip.course) if tip.course else ''
        race_time   = tip.race_time or ''

        position = None
        sp_str   = ''
        sp_dec   = 0.0
        horse_id = ''

        # 1. Check RunnerHistory (permanent historical store)
        rh_query = RunnerHistory.query.filter(
            RunnerHistory.race_date == race_date
        ).all()
        for rh in rh_query:
            if _strip(rh.horse_name) == tip_name:
                if rh.course and _strip(rh.course) != course:
                    continue
                if rh.race_time and race_time and rh.race_time != race_time:
                    continue
                if rh.position:
                    position = rh.position
                    sp_str   = rh.sp or rh.odds or ''
                    horse_id = rh.horse_id or ''
                    try: sp_dec = float(rh.odds or 0)
                    except: pass
                    break

        # 2. Fall back to today's Runner table
        if not position:
            runner = db.session.query(Runner).join(Race).join(Meeting).filter(
                Runner.horse_name.ilike(f'%{tip.horse_name}%'),
                Meeting.date == race_date,
            ).first()
            if runner and runner.position:
                position = runner.position
                sp_str   = runner.sp or runner.odds or ''
                horse_id = runner.horse_id or ''
                try: sp_dec = float(runner.odds or 0)
                except: pass

        if not position:
            continue

        result = _settle(tip, position, sp_dec)
        tr = tip.result
        if not tr:
            tr = TipResult(tip_id=tip.id)
            db.session.add(tr)
        tr.position    = position
        tr.sp          = sp_str
        tr.sp_dec      = sp_dec
        tr.result_type = result['result_type']
        tr.win_pts     = result['win_pts']
        tr.place_pts   = result['place_pts']
        tr.total_pts   = result['total_pts']
        tr.settled_at  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        tip.settled    = True
        if horse_id and not tip.horse_id:
            tip.horse_id = horse_id
        settled_count += 1

    if settled_count:
        db.session.commit()
    return settled_count


@app.route('/webhook/tipster', methods=['POST'])
def tipster_webhook():
    from tip_parser import parse_message
    import hashlib

    # Verify shared secret
    secret = request.headers.get('X-Webhook-Secret', '')
    if TIPSTER_WEBHOOK_SECRET and secret != TIPSTER_WEBHOOK_SECRET:
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    tipster_name = data.get('tipster', 'Turn Of Foot')
    raw_text     = data.get('text', '').strip()
    msg_id       = data.get('message_id', 0)
    msg_datetime = data.get('datetime', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    # Apply midday rule: tips posted at midday or after are for the next day's racing
    if data.get('race_date'):
        race_date = data.get('race_date')
    else:
        try:
            from datetime import timedelta
            msg_dt = datetime.strptime(msg_datetime[:19], '%Y-%m-%d %H:%M:%S')
            race_date = (msg_dt + timedelta(days=1)).strftime('%Y-%m-%d') if msg_dt.hour >= 12 else msg_dt.strftime('%Y-%m-%d')
        except Exception:
            race_date = date.today().strftime('%Y-%m-%d')

    if not raw_text:
        return jsonify({'status': 'ignored', 'reason': 'empty text'})

    tips = parse_message(raw_text)
    if not tips:
        return jsonify({'status': 'ignored', 'reason': 'no tips found'})

    tipster = _get_or_create_tipster(tipster_name)

    # Check for duplicate (same message_id already stored)
    if msg_id and Tip.query.filter_by(telegram_msg_id=msg_id).first():
        return jsonify({'status': 'duplicate'})

    created_tips = []
    uncertain_tips = []

    for t in tips:
        tip = Tip(
            tipster_id       = tipster.id,
            horse_name       = t['horse_name'],
            tip_date         = msg_datetime[:10],
            tip_datetime     = msg_datetime,
            course           = t.get('course', ''),
            race_time        = t.get('race_time', ''),
            race_date        = race_date,
            bet_type         = t.get('bet_type', 'ew'),
            stake_pts        = t.get('stake_pts', 0.5),
            odds             = t.get('odds', ''),
            odds_dec         = t.get('odds_dec', 0.0),
            each_way_places  = t.get('each_way_places', 4),
            each_way_fraction= t.get('each_way_fraction', 5),
            reasoning        = t.get('reasoning', ''),
            raw_message      = raw_text,
            telegram_msg_id  = msg_id,
            uncertain        = t.get('uncertain', False),
            created_at       = datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
        db.session.add(tip)
        db.session.flush()

        # Try to link to HorseProfile
        profile = HorseProfile.query.filter(
            HorseProfile.name.ilike(f'%{t["horse_name"]}%')
        ).first()
        if profile:
            tip.horse_id = profile.horse_id

        created_tips.append({'horse': t['horse_name'], 'odds': t['odds']})
        if t.get('uncertain'):
            uncertain_tips.append(t)

    db.session.commit()

    # Email admin about uncertain tips
    if uncertain_tips:
        try:
            from email_service import send_email
            body = '<br>'.join([
                f"<b>{t.get('horse_name') or 'Unknown'}</b> — {t.get('uncertain_reason','')}<br>"
                f"<pre>{raw_text[:500]}</pre>"
                for t in uncertain_tips
            ])
            send_email(
                ADMIN_EMAIL, 'Admin',
                f'Magnolia Horses: {len(uncertain_tips)} uncertain tip(s) need review',
                f'<html><body><p>The following tips from Turn Of Foot could not be fully parsed:</p>{body}</body></html>'
            )
        except Exception as e:
            print(f'[Tipster] Email error: {e}')

    return jsonify({'status': 'ok', 'tips_created': len(created_tips), 'tips': created_tips})


@app.route('/api/tipster/tips')
@login_required
def get_tips():
    tipster_name = request.args.get('tipster', 'Turn Of Foot')
    per_page     = int(request.args.get('per_page', 500))
    f_bet        = request.args.get('bet_type', '')
    f_settled    = request.args.get('settled', '')
    f_tagged     = request.args.get('tagged', '')
    f_course     = request.args.get('course', '')
    f_jockey     = request.args.get('jockey', '')
    f_colour     = request.args.get('colour', '')

    tipster = Tipster.query.filter_by(name=tipster_name).first()
    if not tipster:
        return jsonify({'tips': [], 'total': 0})

    q = Tip.query.filter_by(tipster_id=tipster.id)
    if f_bet:               q = q.filter(Tip.bet_type == f_bet)
    if f_settled == 'true': q = q.filter(Tip.settled == True)
    if f_settled == 'false':q = q.filter(Tip.settled == False)
    if f_course:            q = q.filter(Tip.course.ilike(f'%{f_course}%'))
    tips = q.order_by(Tip.tip_datetime.desc()).all()

    tagged_set = {th.horse_name.lower() for th in TaggedHorse.query.filter_by(user_id=current_user.id).all()}

    from models import RunnerHistory
    colour_map = {rh.horse_name.lower(): rh.colour
                  for rh in RunnerHistory.query.filter(RunnerHistory.colour != '').with_entities(
                      RunnerHistory.horse_name, RunnerHistory.colour).distinct().all()}
    jockey_map = {rh.horse_name.lower(): rh.jockey
                  for rh in RunnerHistory.query.filter(RunnerHistory.jockey != '').with_entities(
                      RunnerHistory.horse_name, RunnerHistory.jockey).distinct().all()}

    result = []
    for t in tips:
        h       = t.horse_name.lower()
        colour  = colour_map.get(h, '')
        jockey  = jockey_map.get(h, '')
        tagged  = h in tagged_set
        if f_tagged == 'true' and not tagged: continue
        if f_colour and colour.lower() != f_colour.lower(): continue
        if f_jockey and f_jockey.lower() not in jockey.lower(): continue
        result.append({
            'id':              t.id,
            'horse_name':      t.horse_name,
            'tip_date':        t.tip_date,
            'course':          t.course,
            'race_time':       t.race_time,
            'bet_type':        t.bet_type,
            'stake_pts':       t.stake_pts,
            'odds':            t.odds,
            'odds_dec':        t.odds_dec or 0.0,
            'each_way_places': t.each_way_places,
            'reasoning':       t.reasoning,
            'uncertain':       t.uncertain,
            'horse_id':        t.horse_id or '',
            'colour':          colour,
            'jockey':          jockey,
            'tagged':          tagged,
            'settled':         t.settled,
            'result': {
                'position':    t.result.position,
                'sp':          t.result.sp,
                'sp_dec':      t.result.sp_dec or 0.0,
                'result_type': t.result.result_type,
                'win_pts':     t.result.win_pts,
                'place_pts':   t.result.place_pts,
                'total_pts':   t.result.total_pts,
            } if t.result else None,
        })

    return jsonify({'total': len(result), 'tips': result[:per_page]})


@app.route('/api/tipster/stats')
@login_required
def get_tipster_stats():
    tipster_name = request.args.get('tipster', 'Turn Of Foot')
    tipster = Tipster.query.filter_by(name=tipster_name).first()
    if not tipster:
        return jsonify({'error': 'not found'}), 404

    settled = db.session.query(TipResult).join(Tip).filter(
        Tip.tipster_id == tipster.id
    ).all()

    total_pts_staked = 0.0
    total_pts_return = 0.0
    total_pts_return_sp = 0.0
    wins = places = losses = 0
    from tip_parser import settle_tip as _sc

    for r in settled:
        tip = r.tip
        staked = tip.stake_pts * (2 if tip.bet_type == 'ew' else 1)
        total_pts_staked += staked
        total_pts_return += staked + r.total_pts
        if r.sp_dec and r.sp_dec > 1.0:
            total_pts_return_sp += staked + _sc(tip, r.position, r.sp_dec)['total_pts']
        else:
            total_pts_return_sp += staked + r.total_pts
        if r.result_type == 'win':    wins   += 1
        elif r.result_type == 'place': places += 1
        else:                          losses += 1

    total_bets    = len(settled)
    profit_pts    = round(total_pts_return - total_pts_staked, 2)
    profit_pts_sp = round(total_pts_return_sp - total_pts_staked, 2)
    roi_pct       = round(profit_pts / total_pts_staked * 100, 1) if total_pts_staked else 0.0
    roi_pct_sp    = round(profit_pts_sp / total_pts_staked * 100, 1) if total_pts_staked else 0.0

    # Monthly breakdown
    monthly = {}
    for r in settled:
        month = (r.tip.tip_date or '')[:7]  # YYYY-MM
        if month not in monthly:
            monthly[month] = {'staked': 0.0, 'return': 0.0, 'return_sp': 0.0, 'bets': 0}
        staked = r.tip.stake_pts * (2 if r.tip.bet_type == 'ew' else 1)
        monthly[month]['staked'] += staked
        monthly[month]['return'] += staked + r.total_pts
        monthly[month]['return_sp'] += staked + (_sc(r.tip, r.position, r.sp_dec)['total_pts'] if r.sp_dec and r.sp_dec > 1.0 else r.total_pts)
        monthly[month]['bets'] += 1

    monthly_list = sorted([
        {
            'month':   k,
            'bets':    v['bets'],
            'staked':  round(v['staked'], 2),
            'profit':    round(v['return'] - v['staked'], 2),
            'profit_sp': round(v['return_sp'] - v['staked'], 2),
            'roi':       round((v['return'] - v['staked']) / v['staked'] * 100, 1) if v['staked'] else 0.0,
        }
        for k, v in monthly.items()
    ], key=lambda x: x['month'])

    return jsonify({
        'tipster':      tipster_name,
        'total_bets':   total_bets,
        'wins':         wins,
        'places':       places,
        'losses':       losses,
        'unsettled':    Tip.query.filter_by(tipster_id=tipster.id, settled=False).count(),
        'staked_pts':   round(total_pts_staked, 2),
        'profit_pts':    profit_pts,
        'profit_pts_sp': profit_pts_sp,
        'roi_pct':       roi_pct,
        'roi_pct_sp':    roi_pct_sp,
        'monthly':      monthly_list,
    })


@app.route('/api/admin/settle-tips', methods=['POST'])
@login_required
def admin_settle_tips():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    count = _settle_pending_tips()
    return jsonify({'status': 'ok', 'settled': count})


@app.route('/api/admin/backfill-tips', methods=['POST'])
@login_required
def admin_backfill_tips():
    """Accept a JSON array of pre-parsed tip dicts for backfill.
    If messages is empty, uses the embedded historical message list."""
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    from tip_parser import parse_message
    data = request.get_json() or {}
    MESSAGES = [
    # June 16 2026
    {"datetime": "2026-06-16 08:31:00", "race_date": "2026-06-16", "text": """Ascot (today)

3.05 - God Given Talent 0.5pt E/w 33/1 with 4 places

Another for the Coventry at a big price. God Given Talent hit the line well at Newbury despite being both green and unfancied in the market. He posted a good time and is reported to have come on leaps and bounds since then. With Buick booked, he's an interesting outsider for sure.

5.00 - Bahadur 0.5pt E/w 20/1 with 5 places

This is a much tougher assignment than his Goodwood win last time out, but he comes out well on all three sets of ratings I refer to and is a big E/w play as such, if he gets home over the additional 4f distance. As is the case for many of these, stamina is the biggest risk factor over a quirky trip for the Flat.

5.35 - Ghostwriter, Royal Rhyme 0.5pt E/w 9/1, 12/1 with 4 places

Ghostwriter comes here fresh but did have a racecourse gallop recently. He's reported to be in fine form and boasts some of the best form on offer here. Should run a big race. Royal Rhyme was also entered in the Prince of Wales's, but has his sights lowered to a more realistic level for this Listed contest. He's had two nice tune-up runs and needs to kick on now. If he can improve on those two efforts, he too is right in the picture for Karl Burke.

6.10 - Paddy The Squire 0.5pt E/w 28/1 with 5 places

A big, lazy sort who has climbed the middle distance ranks well of the past 12 months and he could have more to offer yet. His main aim is the Ebor, but he caught the eye at York over an inadequate trip last time out and he should be sharper for that outing here. Nicely drawn in stall 4, I'm hoping he can nestle into a nice position early, settle well, and give a fair account of himself when the pressure comes on into the home straight."""},

    # June 17 2026 Part 1
    {"datetime": "2026-06-17 08:29:00", "race_date": "2026-06-17", "text": """Royal Ascot (Part 1)

2.30 - Alta Regina 0.5pt E/w 13/2 with 4 places

She's been well backed but looks sure to run a big run from a plumb high draw on figures and appearance, she looks the ideal type of the race. It's a very hot renewal with an endless stream of contenders, but I'm hopeful Alta Regina has the touch of quality needed to separate herself from the majority of these quick fillies.

3.05 - Del Maro 0.5pt E/w 14/1

Del Maro is a good looking son of Camelot who shapes as though the step up to 1m6f could really suit. He brings a good blend of form and experience to the table, coming out nicely on all three ratings I use and he's an agreeable price from a nice draw with Will Buick doing the steering.

4.20 - Almaqam 0.5pt E/w 7/1

A good winner at the Curragh for us last time out. This is a significantly tougher assignment, but Bay City Roller franked the form nicely at Epsom and Ed Walker's gelding has done very little wrong. He's a pound or two below the best of Ombudsman and the French lad, but he's going the right way and I'd expect another big run today."""},

    # June 17 2026 Part 2
    {"datetime": "2026-06-17 09:37:00", "race_date": "2026-06-17", "text": """Royal Ascot (Part 2)

5.00 - Checkandchallenge 0.5pt E/w 20/1 with 6 places

I've had this fella on my mind for the Hunt Cup since his eye-catching run at Newbury two starts back. I've followed him for a while and still feel he's capable of landing a big one some day. He wears cheekpieces here which can help, he's well drawn and hopefully all goes well in the pre-lims etc. All being well he can run on strongly late to hit the frame.

5.35 - American Gal 0.5pt E/w 33/1 with 5 places

American Gal ran no race at Newmarket and her form figures don't inspire confidence, but most of her runs have been at a much higher grade and she makes her handicap debut off a nice mark of 97. She's gone well at Ascot before and is a big enough price to take a chance on.

6.10 - Controlla 1pt win 4/1

Her RPR/RP Topspeed combo of 96/94 stands out like a sore thumb here. Her experience over 6f is valuable and she ticks all the boxes for a win bet in a field lacking depth. There's 3-4 horses that could improve and we also have to hope Controlla handles the occasion and the track. But if the stars align, she's going to be very hard to beat."""},

    # June 18 2026
    {"datetime": "2026-06-18 08:29:00", "race_date": "2026-06-18", "text": """Ripon

2.15 - State Of Gold 0.5pt E/w 11/2

This filly gets a bit of weight from the boys and posted a good set of numbers at York, which entitle her to be involved at the finish for this 6f novice.

Southwell

7.30 - Sheikhnshah 0.5pt E/w 10/1

Centigrade has been off for over 600 days and carries a big weight here. I'm hopeful Sheikhshah is at a nice price point at 10/1.

Leopardstown

7.50 - Eniac 0.5pt E/w 7/1

Changing Lanes sets a fair bar but Eniac is right on collateral form terms with the favourite on a line through Bay Of Stars."""},

    # June 18 2026 Royal Ascot
    {"datetime": "2026-06-18 09:41:00", "race_date": "2026-06-18", "text": """Royal Ascot

2.30 - Aperoll 0.5pt E/w 11/1 with 4 places

Quite an open and interesting renewal of the Chesham here, where Richard Hannon's Aperoll has bright E/w claims after a good win at Newbury on debut.

3.05 - Golden Knight, Joulany 0.5pt E/w 12/1, 14/1 with 5 places

Golden Knight is very interesting here off a mark of 87. His win at Newmarket has worked out seriously well. Joulany comes up well on the clock here and is going the right way.

4.15 - Rahiebb 0.5pt E/w 13/2

It was a nice performance in the Yorkshire Cup last time out and Rahiebb looks to me like he'll stay all day.

4.50 - Crest Of Fire, Wise Prince 0.5pt E/w 25/1, 40/1 with 6 places

Crest Of Fire stayed on well late in the day at Carlisle. Wise Prince is a lovely looking horse who steps down in class for this handicap debut.

5.35 - Oceans Four 0.5pt E/w 100/1

Shooting for the moon a bit here but Oceans Four is on my list of horses to follow this season.

6.10 - Royal Velvet 0.5pt E/w 16/1 with 5 places

Love this mare, she's been a good servant to the Mainline and arrives here in seriously good order."""},

    # June 19 2026
    {"datetime": "2026-06-19 08:29:00", "race_date": "2026-06-19", "text": """Ascot

2.30 - Libertango, Dark Issue 0.5pt E/w 10/1, 16/1 with 4 places

Sun Goddess arrives with a standout RPR of 92, which puts her a handful of pounds ahead of these on form numbers.

4.20 - Touleen 0.5pt E/w 10/1

It will be difficult to penetrate the O'Brien fillies, but Touleen is the only other in the field with the right blend of performance numbers.

5.00 - Rosa Inglesa, True Test 0.5pt E/w 12/1, 40/1 with 6 places

Rosa Inglesa gets in here off a low weight after quickening up stylishly to score last time out.

Limerick

4.47 - Expert Dancer 1pt win 3/1

Horses can always improve on a whim in this sphere, but based on all we know, 3/1 underestimates Expert Dancer's chances of winning this 7f maiden."""},

    # June 19 Saturday tips
    {"datetime": "2026-06-19 18:51:00", "race_date": "2026-06-20", "text": """Royal Ascot

2.30 - Force Noir 0.5pt E/w 11/1 with 4 places

3.40 - Comanche Brave 0.5pt E/w 14/1 with 4 places

4.20 - Thesecretadversary, Andab 0.5pt E/w 10/1, 22/1 with 4 places

5.00 - Ten Pounds, Sondad, Far Above Dream 0.5pt E/w 11/1, 18/1, 18/1 with 6 places

Newmarket

2.36 - Alfaraz 1pt win 7/2

Redcar

1.42 - Furturra 1pt win 2/1

2.12 - Undercover Affair 1pt win 4/1

Doncaster

5.48 - Cash Cove 0.5pt E/w 7/1"""},

    # June 25 2026
    {"datetime": "2026-06-25 08:33:00", "race_date": "2026-06-25", "text": """Newcastle

4.22 - Aura Of Melania 0.5pt E/w 11/1

Improved nicely from her first run and with a bit more progression here she comes up well enough on the numbers to hit the frame.

Newmarket

12.15 - The Can Can Queen 0.5pt E/w 33/1

She may well be outclassed but she'll certainly appreciate the step back up to 6f today."""},

    # June 25 Saturday tips
    {"datetime": "2026-06-25 18:01:00", "race_date": "2026-06-26", "text": """Newcastle

2.10 - Heavenly Heather 0.5pt E/w 11/1 with 4 places

Ran to an RPR of 116 at Royal Ascot and if she can repeat that in any fashion here she won't be miles away.

3.15 - Zanndabad, Align The Stars 0.5pt E/w 14/1, 18/1 with 5 places

Both have interesting profiles for the race and are in my notebook.

York

1.55 - Andesite, Frankies Dream 0.5pt E/w 8/1 apiece with 5 places

Andesite was an eyecatcher here last time out. Frankie is consistent and looks ahead of the handicapper.

2.25 - Our Cody 0.5pt E/w 9/1 with 4 places

Tricky to win with clearly, but has a bit of talent behind the form figures.

Curragh

3.20 - Velozee 0.5pt E/w 17/2 with 5 places

Ran well at Ascot and wasn't given a hard time.

3.55 - Venetian Lace 0.5pt E/w 14/1

Ground went against her and we learned nothing at Epsom. Back to 1m2f is more within range."""},

    # July 2 2026
    {"datetime": "2026-07-02 23:33:00", "race_date": "2026-07-03", "text": """Chepstow

6.15 - Deadline 0.5pt E/w 13/2

Doncaster

2.00 - Mobadir 0.5pt E/w 22/1

Sandown

2.25 - Bill The Bull 0.5pt E/w 5/1

3.00 - Cilician 1pt win 5/2

3.35 - Royal Rhyme 1pt win 11/2

5.15 - Silca Bay, H Key Lails 0.5pt E/w 10/1, 25/1"""},

    # July 3 evening
    {"datetime": "2026-07-03 19:58:00", "race_date": "2026-07-04", "text": """Sandown

2.25 - Ebt's Guard, Bourbon Blues 0.5pt E/w 12/1, 20/1 5 places

Newmarket

3.15 - Paddy The Squire 0.5pt E/w 15/2 with 4 places"""},

    # July 9 2026
    {"datetime": "2026-07-09 09:14:00", "race_date": "2026-07-09", "text": """Newmarket

3.00 - Calico Blue, Ten Carat Harry 0.5pt E/w 15/2, 11/1 with 4 places

Both ran in the same race at Royal Ascot and are interesting for different reasons.

4.10 - Tall Trees 0.5pt E/w 15/2

Tall Trees was an eyecatcher at Royal Ascot when running on late behind our nice winner Libertango.

Leopardstown

7.03 - Lord Massusus 0.5pt E/w 25/1 with 4 places

This experienced grey comes out well on all three sets of numbers I refer to.

Newbury

5.45 - My Normandie 0.5pt E/w 10/1

May be seen to better effect once handicapping but I am led by numbers and evidence.

Epsom

7.50 - Balon D'or 0.5pt E/w 16/1

16/1 seems a big price with the three places provided the dead eight remain."""},

    # July 9 evening
    {"datetime": "2026-07-09 18:28:00", "race_date": "2026-07-10", "text": """Newmarket

3.00 - Roaring Legend 0.5pt E/w 40/1

Strong stayer Roaring Legend isn't out of the place picture.

3.35 - Venetian Lace 0.5pt E/w 25/1

The Oaks project didn't work out, but she posted a quick time in the Guineas.

4.45 - Tatterstall 0.5pt E/w 8/1 with 4 places

Tatterstall went off the boil a bit but he's been back to something like his best in recent outings.

York

2.10 - Andesite 0.5pt E/w 5/1 with 5 places

Andesite looked like a horse ready to land a good pot or two last time out.

2.45 - America Queen, Hold A Dream 0.5pt E/w 9/1, 50/1 with 4 places

Spicy Marg is a solid favourite but I think race conditions are going to suit America Queen."""},

    # July 10 Saturday
    {"datetime": "2026-07-10 12:58:00", "race_date": "2026-07-12", "text": """Newmarket (Sat)

4.00 - Pikachu 1pt win 9/1

Pikachu ran to an RPR & RP Topspeed combination of 98/89 when 5th in the Chesham at Royal Ascot.

4.35 - Big Mojo 0.5pt E/w 10/1

It looks a tremendous renewal of the July Cup. I'm willing to give Big Mojo another chance to shine.

York (Sat)

2.39 - Checkandchallenge 0.5pt E/w 12/1 with 4 places

You might be thinking when are we getting off this Checkandchallenge ride?

3.12 - Heavenly Heather 0.5pt E/w 15/2

She was a non-runner the other day in a slightly tougher race, though this is an open and quality affair too.

3.45 - Castle Stuart 0.5pt E/w 16/1 with 5 places

Castle Stuart looks nicely weighted for this contest on the back of an excellent course and distance effort last time."""},

    # July 13 2026
    {"datetime": "2026-07-13 08:55:00", "race_date": "2026-07-13", "text": """Windsor

5.50 - Asset 0.5pt E/w 7/1

Asset has eased from 4/1 to 7/1. In receipt of 5lb and 10lb respectively, her performance numbers put her bang there with them."""},

    # July 15 2026
    {"datetime": "2026-07-15 08:44:00", "race_date": "2026-07-15", "text": """Bath

3.01 - Sheer Beauty 0.5pt E/w 7/1

Sheer Beauty is a nippy filly who shaped quite well for a long way at Windsor and Bath could very much be to her liking.

Killarney

7.00 - Wild Bessie 0.5pt E/w 50/1 with 4 places

A hot 12-runner Listed race. Wild Bessie comes out quite favourably on the clock."""},

    # July 18 2026
    {"datetime": "2026-07-18 08:29:00", "race_date": "2026-07-18", "text": """Newbury

3.37 - Vollering 0.5pt E/w 8/1 with 5 places

Vollering is a quick and experienced 2yo who wears her heart on her sleeve from the front.

Market Rasen

2.10 - Morning Mayhem, Howth 0.5pt E/w 25/1, 40/1 with 4 places

I avoid jumps like the plague at this time of year but it's not too often you get a chance in a valuable race.

Curragh

3.25 - Cover Up 0.5pt E/w 8/1

Veteran Cover Up was an eye-catcher behind Mission Control at Royal Ascot.

Newmarket

3.05 - Little Dorrit 0.5pt E/w 9/1

Her one start so far this season was a promising effort and she's entitled to come on a bundle."""},

    # July 18 update
    {"datetime": "2026-07-18 10:48:00", "race_date": "2026-07-18", "text": """Newbury

1.55 - Ocean's Four 0.5pt E/w 6/1

He ran a big one for us at huge odds the last day and any improvement on that would see him go close.

Newmarket

4.17 - Generous Rascal 0.5pt E/w 18/1

The return to 6f is a positive and he's becoming nicely handicapped in this sort of grade.

5.20 - Brisk Symphony 0.5pt E/w 8/1

None of these look particularly well handicapped and I'm hopeful Brisk Symphony can produce something more like her Lingfield and Yarmouth runs.

Ripon

5.03 - Mohmentous 1pt win 9/2

Mohmentous stands out quite a bit on RP speed figures here and looks a big price at 9/2."""},

    # July 19 2026
    {"datetime": "2026-07-19 08:32:00", "race_date": "2026-07-19", "text": """Curragh

3.15 - Power Blue 1pt win 2/1 generally

Power Blue jumps out at every touch point. A Group 1 winner over 6f at the track, he's competed at the same level over a mile in recent outings, producing superior figures to this field."""},
]
    messages = data.get('messages') or MESSAGES
    tipster = _get_or_create_tipster('Turn Of Foot')
    created = 0
    data_json = request.get_json() or {}

    # Mode 1: pre-parsed tips passed directly
    pre_parsed = data_json.get('tips', [])
    if pre_parsed:
        for t in pre_parsed:
            msg_id = t.get('message_id', 0)
            if msg_id and Tip.query.filter_by(telegram_msg_id=msg_id).first():
                continue
            if not t.get('horse_name'):
                continue
            msg_datetime = t.get('datetime', '')
            tip = Tip(
                tipster_id       = tipster.id,
                horse_name       = t['horse_name'],
                tip_date         = msg_datetime[:10],
                tip_datetime     = msg_datetime,
                course           = t.get('course', ''),
                race_time        = t.get('race_time', ''),
                race_date        = t.get('race_date', msg_datetime[:10]),
                bet_type         = t.get('bet_type', 'ew'),
                stake_pts        = t.get('stake_pts', 0.5),
                odds             = t.get('odds', ''),
                odds_dec         = t.get('odds_dec', 0.0),
                each_way_places  = t.get('each_way_places', 4),
                each_way_fraction= t.get('each_way_fraction', 5),
                reasoning        = t.get('reasoning', ''),
                raw_message      = t.get('text', ''),
                telegram_msg_id  = msg_id,
                uncertain        = t.get('uncertain', False),
                created_at       = datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            )
            db.session.add(tip)
            created += 1
            if created % 50 == 0:
                db.session.flush()
        db.session.commit()
        _settle_pending_tips()
        return jsonify({'status': 'ok', 'created': created})

    # Mode 2: raw messages to parse
    messages = data_json.get('messages') or MESSAGES
    for msg in messages:
        raw_text     = msg.get('text', '')
        msg_datetime = msg.get('datetime', '')
        race_date    = msg.get('race_date', msg_datetime[:10] if msg_datetime else '')
        msg_id       = msg.get('message_id', 0)
        if msg_id and Tip.query.filter_by(telegram_msg_id=msg_id).first():
            continue
        tips = parse_message(raw_text)
        for t in tips:
            if t.get('uncertain') and not t.get('horse_name'):
                continue
            tip = Tip(
                tipster_id       = tipster.id,
                horse_name       = t['horse_name'],
                tip_date         = msg_datetime[:10],
                tip_datetime     = msg_datetime,
                course           = t.get('course', ''),
                race_time        = t.get('race_time', ''),
                race_date        = race_date,
                bet_type         = t.get('bet_type', 'ew'),
                stake_pts        = t.get('stake_pts', 0.5),
                odds             = t.get('odds', ''),
                odds_dec         = t.get('odds_dec', 0.0),
                each_way_places  = t.get('each_way_places', 4),
                each_way_fraction= t.get('each_way_fraction', 5),
                reasoning        = t.get('reasoning', ''),
                raw_message      = raw_text,
                telegram_msg_id  = msg_id,
                uncertain        = t.get('uncertain', False),
                created_at       = datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            )
            db.session.add(tip)
            created += 1
        db.session.flush()
    db.session.commit()
    _settle_pending_tips()
    return jsonify({'status': 'ok', 'created': created})


if __name__ == '__main__':
    app.run(debug=True, port=5001)
