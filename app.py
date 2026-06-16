from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from models import db, User, TaggedHorse, SavedSearch, EmailLog, Meeting, Race, Runner, ColourOverride, SyncLog, HorseProfile, HorseRun, HorseRunField
from sync import sync_todays_races, sync_horse_history, backfill_horse_history
from email_service import send_morning_alerts
import json
import os
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


scheduler = BackgroundScheduler()
scheduler.add_job(func=lambda: sync_todays_races(app), trigger='interval', minutes=15)
scheduler.add_job(func=lambda: sync_and_alert(app), trigger='cron', hour=5, minute=0)
scheduler.add_job(func=lambda: sync_horse_history(app), trigger='cron', hour=23, minute=0)
scheduler.start()


def is_admin():
    return current_user.is_authenticated and current_user.email == ADMIN_EMAIL


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', is_admin=is_admin())


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
        if f.get('horse'):   parts.append(f"Horse: {f['horse']}")
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
    return render_template('my_horses.html', tagged=tagged,
                           running_today=running_today, searches=searches_display)


@app.route('/account')
@login_required
def account():
    try:
        logs = EmailLog.query.filter_by(user_id=current_user.id)\
            .order_by(EmailLog.id.desc()).limit(10).all()
    except Exception:
        db.create_all()
        logs = []
    return render_template('account.html', logs=logs, is_admin=is_admin())


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
            if f.get('horse'):   parts.append(f"Horse: {f['horse']}")
            if f.get('jockey'):  parts.append(f"Jockey: {f['jockey']}")
            if f.get('trainer'): parts.append(f"Trainer: {f['trainer']}")
            if f.get('colour'):  parts.append(f"Colour: {f['colour']}")
            if f.get('meeting'): parts.append(f"Meeting: {f['meeting']}")
            if f.get('uk_only'): parts.append('UK only')
            searches.append({
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
        })
    return render_template('admin_users.html', users=users_data)


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


@app.route('/api/saved-searches/<int:search_id>', methods=['DELETE'])
@login_required
def delete_saved_search(search_id):
    s = SavedSearch.query.filter_by(id=search_id, user_id=current_user.id).first()
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

@app.route('/api/ai-horse-search', methods=['POST'])
def ai_horse_search():
    data    = request.get_json() or {}
    term    = (data.get('term') or '').strip()
    uk_only = data.get('uk_only', True)

    if not term:
        return jsonify({'error': 'term required'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY') or os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured on server'}), 500

    today     = date.today().strftime('%Y-%m-%d')
    cache_key = f"{today}|{term.lower()}|{'uk' if uk_only else 'all'}"

    if cache_key in _ai_cache:
        return jsonify({'names': _ai_cache[cache_key], 'cached': True})

    # Fetch horse names
    all_runners = db.session.query(Runner).join(Race).join(Meeting)\
        .filter(Meeting.date == today).all()

    if uk_only:
        all_runners = [r for r in all_runners if is_uk_course(r.race.meeting.name)]

    all_names = list({r.horse_name for r in all_runners})

    if not all_names:
        return jsonify({'names': [], 'cached': False})

    try:
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

        # Validate against actual names
        valid_set = {n.lower(): n for n in all_names}
        matched   = [valid_set[m.lower()] for m in matched if m.lower() in valid_set]

        _ai_cache[cache_key] = matched
        return jsonify({'names': matched, 'cached': False})

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
        'tagged':          r.horse_name.lower() in tagged_map,
        'notes':           tagged_map.get(r.horse_name.lower(), ''),
    }


def build_race_obj(r_data, tagged_map):
    race          = r_data['race']
    runners       = r_data['runners']
    is_result     = (race.race_status or '').lower() == 'result'
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
    matched_ids = {}
    for saved in searches:
        try:
            f = json.loads(saved.filters)
        except Exception:
            continue
        for r in all_runners:
            if f.get('uk_only') and not is_uk_course(r.race.meeting.name): continue
            if f.get('colour')  and f['colour'].lower()  not in (r.colour or '').lower():  continue
            if f.get('meeting') and f['meeting'].lower() not in r.race.meeting.name.lower(): continue
            if f.get('jockey')  and f['jockey'].lower()  != (r.jockey or '').lower():       continue
            if f.get('trainer') and f['trainer'].lower() != (r.trainer or '').lower():       continue
            if f.get('owner')   and f['owner'].lower()   != (r.owner or '').lower():         continue
            hf = (f.get('horse') or '').strip()
            if hf and hf.lower() not in r.horse_name.lower():
                continue
            matched_ids[r.id] = r
    matched = list(matched_ids.values())
    if matched:
        return jsonify(sort_by_meeting(matched, tagged_map))
    return jsonify([])


@app.route('/api/sync', methods=['POST'])
@login_required
def manual_sync():
    if not is_admin():
        return jsonify({'error': 'Forbidden'}), 403
    sync_todays_races(app)
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


@app.route('/admin/colours')
def admin_colours():
    return render_template('admin_colours.html')


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


if __name__ == '__main__':
    app.run(debug=True, port=5001)
