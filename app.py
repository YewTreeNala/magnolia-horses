from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from models import db, User, TaggedHorse, Meeting, Race, Runner, ColourOverride
from sync import sync_todays_races
from email_service import send_morning_alerts
from rapidfuzz import fuzz
import jellyfish
import os
from datetime import datetime

load_dotenv()

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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()

scheduler = BackgroundScheduler()
scheduler.add_job(func=lambda: sync_todays_races(app), trigger='interval', hours=1)
scheduler.add_job(func=lambda: send_morning_alerts(app), trigger='cron', hour=7, minute=0)
scheduler.start()


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


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
    tagged = TaggedHorse.query.filter_by(user_id=current_user.id)\
        .order_by(TaggedHorse.horse_name).all()

    from datetime import date
    today        = date.today().strftime('%Y-%m-%d')
    tagged_names = [t.horse_name.lower() for t in tagged]
    tagged_notes = {t.horse_name.lower(): t.notes for t in tagged}

    running_today = []
    if tagged_names:
        runners = db.session.query(Runner).join(Race).join(Meeting)\
            .filter(Meeting.date == today).all()
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
                })

    return render_template('my_horses.html', tagged=tagged, running_today=running_today)


@app.route('/account')
@login_required
def account():
    return render_template('account.html')


# ── Tag API ───────────────────────────────────────────────────────────────────

@app.route('/api/tag', methods=['POST'])
@login_required
def tag_horse():
    data       = request.get_json()
    horse_name = (data or {}).get('horse_name', '').strip()
    notes      = (data or {}).get('notes', '').strip()
    if not horse_name:
        return jsonify({'error': 'horse_name required'}), 400

    existing = TaggedHorse.query.filter_by(
        user_id=current_user.id, horse_name=horse_name
    ).first()
    if existing:
        # Update notes if already tagged
        existing.notes = notes
        db.session.commit()
        return jsonify({'status': 'updated', 'horse_name': horse_name})

    tag = TaggedHorse(
        user_id=current_user.id,
        horse_name=horse_name,
        notes=notes,
        tagged_at=datetime.now().strftime('%Y-%m-%d %H:%M')
    )
    db.session.add(tag)
    db.session.commit()
    return jsonify({'status': 'tagged', 'horse_name': horse_name})


@app.route('/api/untag', methods=['POST'])
@login_required
def untag_horse():
    data       = request.get_json()
    horse_name = (data or {}).get('horse_name', '').strip()
    tag        = TaggedHorse.query.filter_by(
        user_id=current_user.id, horse_name=horse_name
    ).first()
    if tag:
        db.session.delete(tag)
        db.session.commit()
    return jsonify({'status': 'untagged', 'horse_name': horse_name})


@app.route('/api/my-tags')
@login_required
def my_tags():
    tags = TaggedHorse.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'horse_name': t.horse_name,
        'notes':      t.notes or ''
    } for t in tags])


@app.route('/api/tag-notes', methods=['POST'])
@login_required
def update_tag_notes():
    data       = request.get_json()
    horse_name = (data or {}).get('horse_name', '').strip()
    notes      = (data or {}).get('notes', '').strip()
    tag        = TaggedHorse.query.filter_by(
        user_id=current_user.id, horse_name=horse_name
    ).first()
    if tag:
        tag.notes = notes
        db.session.commit()
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'not found'}), 404


# ── Data API ──────────────────────────────────────────────────────────────────

@app.route('/api/options')
def options():
    jockeys  = db.session.query(Runner.jockey).filter(Runner.jockey != '', Runner.jockey != None).distinct().order_by(Runner.jockey).all()
    trainers = db.session.query(Runner.trainer).filter(Runner.trainer != '', Runner.trainer != None).distinct().order_by(Runner.trainer).all()
    owners   = db.session.query(Runner.owner).filter(Runner.owner != '', Runner.owner != None).distinct().order_by(Runner.owner).all()
    return jsonify({
        'jockeys':  [r[0] for r in jockeys],
        'trainers': [r[0] for r in trainers],
        'owners':   [r[0] for r in owners],
    })


@app.route('/api/search')
def search():
    horse   = request.args.get('horse', '').strip()
    trainer = request.args.get('trainer', '').strip()
    jockey  = request.args.get('jockey', '').strip()
    colour  = request.args.get('colour', '').strip()
    meeting = request.args.get('meeting', '').strip()
    owner   = request.args.get('owner', '').strip()
    date    = request.args.get('date', '').strip()
    sort    = request.args.get('sort', 'meeting').strip()

    query = db.session.query(Runner).join(Race).join(Meeting)
    if trainer: query = query.filter(Runner.trainer == trainer)
    if jockey:  query = query.filter(Runner.jockey == jockey)
    if colour:  query = query.filter(Runner.colour.ilike(f'%{colour}%'))
    if meeting: query = query.filter(Meeting.name.ilike(f'%{meeting}%'))
    if owner:   query = query.filter(Runner.owner == owner)
    if date:    query = query.filter(Meeting.date == date)

    runners = query.order_by(Meeting.name, Race.time, Runner.number).all()

    if horse:
        def is_match(name, search):
            name_l = name.lower(); search_l = search.lower()
            if search_l in name_l: return True
            if fuzz.partial_ratio(search_l, name_l) >= 75: return True
            for word in name_l.split():
                for sword in search_l.split():
                    if len(word) > 2 and len(sword) > 2:
                        if jellyfish.soundex(word) == jellyfish.soundex(sword):
                            return True
            return False
        runners = [r for r in runners if is_match(r.horse_name, horse)]

    tagged_map = {}
    if current_user.is_authenticated:
        for t in TaggedHorse.query.filter_by(user_id=current_user.id).all():
            tagged_map[t.horse_name.lower()] = t.notes or ''

    if sort == 'time':
        return jsonify(sort_by_time(runners, tagged_map))
    else:
        return jsonify(sort_by_meeting(runners, tagged_map))


def build_race_obj(r_data, tagged_map):
    return {
        'time':     r_data['race'].time,
        'name':     r_data['race'].name,
        'distance': r_data['race'].distance,
        'class':    r_data['race'].race_class,
        'runners': [{
            'number':  r.number,
            'name':    r.horse_name,
            'colour':  r.colour,
            'age':     r.age,
            'sex':     r.sex,
            'trainer': r.trainer,
            'jockey':  r.jockey,
            'owner':   r.owner,
            'form':    r.form,
            'weight':  r.weight,
            'or':      r.official_rating,
            'odds':    r.odds,
            'tagged':  r.horse_name.lower() in tagged_map,
            'notes':   tagged_map.get(r.horse_name.lower(), ''),
        } for r in r_data['runners']]
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
        if t not in time_groups: time_groups[t] = []
        time_groups[t].append(r_data)
    result = []
    for time_slot in sorted(time_groups.keys()):
        for r_data in time_groups[time_slot]:
            race = r_data['race']
            result.append({'meeting': race.meeting.name, 'date': race.meeting.date, 'races': [build_race_obj(r_data, tagged_map)]})
    return result


@app.route('/api/sync', methods=['POST'])
def manual_sync():
    sync_todays_races(app)
    return jsonify({'status': 'ok'})


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
    return jsonify([{'horse_name': r.horse_name, 'colour': r.colour, 'has_override': r.horse_name.lower() in overrides, 'meeting': r.race.meeting.name, 'race': r.race.name} for r in runners])


@app.route('/api/colours/override', methods=['POST'])
def set_colour_override():
    data = request.get_json()
    horse_name = data.get('horse_name', '').strip()
    colour     = data.get('colour', '').strip()
    if not horse_name or not colour:
        return jsonify({'error': 'required'}), 400
    override = ColourOverride.query.filter_by(horse_name=horse_name).first()
    if override:
        override.colour = colour; override.updated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    else:
        db.session.add(ColourOverride(horse_name=horse_name, colour=colour, updated_at=datetime.now().strftime('%Y-%m-%d %H:%M')))
    for r in Runner.query.filter(Runner.horse_name.ilike(horse_name)).all(): r.colour = colour
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/colours/overrides')
def list_overrides():
    return jsonify([{'horse_name': o.horse_name, 'colour': o.colour, 'updated_at': o.updated_at} for o in ColourOverride.query.order_by(ColourOverride.horse_name).all()])


@app.route('/api/colours/override/<horse_name>', methods=['DELETE'])
def delete_override(horse_name):
    override = ColourOverride.query.filter_by(horse_name=horse_name).first()
    if override:
        db.session.delete(override)
        db.session.commit()
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    app.run(debug=True, port=5001)
