from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from models import db, Meeting, Race, Runner, ColourOverride
from sync import sync_todays_races
from rapidfuzz import fuzz
import jellyfish
import os
from datetime import datetime

load_dotenv()

app = Flask(__name__)

# Database — SQLite locally, PostgreSQL in production
database_url = os.getenv('DATABASE_URL', 'sqlite:///racing.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()

scheduler = BackgroundScheduler()
scheduler.add_job(
    func=lambda: sync_todays_races(app),
    trigger='interval',
    hours=1
)
scheduler.start()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/admin/colours')
def admin_colours():
    return render_template('admin_colours.html')


@app.route('/api/options')
def options():
    jockeys = db.session.query(Runner.jockey)\
        .filter(Runner.jockey != '', Runner.jockey != None)\
        .distinct().order_by(Runner.jockey).all()

    trainers = db.session.query(Runner.trainer)\
        .filter(Runner.trainer != '', Runner.trainer != None)\
        .distinct().order_by(Runner.trainer).all()

    owners = db.session.query(Runner.owner)\
        .filter(Runner.owner != '', Runner.owner != None)\
        .distinct().order_by(Runner.owner).all()

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

    if trainer:
        query = query.filter(Runner.trainer == trainer)
    if jockey:
        query = query.filter(Runner.jockey == jockey)
    if colour:
        query = query.filter(Runner.colour.ilike(f'%{colour}%'))
    if meeting:
        query = query.filter(Meeting.name.ilike(f'%{meeting}%'))
    if owner:
        query = query.filter(Runner.owner == owner)
    if date:
        query = query.filter(Meeting.date == date)

    runners = query.order_by(Meeting.name, Race.time, Runner.number).all()

    if horse:
        def is_match(name, search):
            name_l   = name.lower()
            search_l = search.lower()
            if search_l in name_l:
                return True
            if fuzz.partial_ratio(search_l, name_l) >= 75:
                return True
            for word in name_l.split():
                for sword in search_l.split():
                    if len(word) > 2 and len(sword) > 2:
                        if jellyfish.soundex(word) == jellyfish.soundex(sword):
                            return True
            return False
        runners = [r for r in runners if is_match(r.horse_name, horse)]

    if sort == 'time':
        return jsonify(sort_by_time(runners))
    else:
        return jsonify(sort_by_meeting(runners))


@app.route('/api/colours/runners')
def colour_runners():
    """Return all runners with their current colour for the admin page."""
    search = request.args.get('q', '').strip().lower()
    query  = db.session.query(Runner).join(Race).join(Meeting)
    if search:
        query = query.filter(Runner.horse_name.ilike(f'%{search}%'))
    runners = query.order_by(Runner.horse_name).limit(100).all()

    overrides = {
        o.horse_name.lower(): o.colour
        for o in ColourOverride.query.all()
    }

    return jsonify([{
        'horse_name':  r.horse_name,
        'colour':      r.colour,
        'has_override': r.horse_name.lower() in overrides,
        'meeting':     r.race.meeting.name,
        'race':        r.race.name,
    } for r in runners])


@app.route('/api/colours/override', methods=['POST'])
def set_colour_override():
    """Save a manual colour correction for a horse."""
    data       = request.get_json()
    horse_name = data.get('horse_name', '').strip()
    colour     = data.get('colour', '').strip()

    if not horse_name or not colour:
        return jsonify({'error': 'horse_name and colour are required'}), 400

    # Update existing override or create new one
    override = ColourOverride.query.filter_by(horse_name=horse_name).first()
    if override:
        override.colour     = colour
        override.updated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    else:
        override = ColourOverride(
            horse_name=horse_name,
            colour=colour,
            updated_at=datetime.now().strftime('%Y-%m-%d %H:%M')
        )
        db.session.add(override)

    # Also update the runner in today's database immediately
    runners = Runner.query.filter(
        Runner.horse_name.ilike(horse_name)
    ).all()
    for r in runners:
        r.colour = colour

    db.session.commit()
    return jsonify({'status': 'ok', 'horse_name': horse_name, 'colour': colour})


@app.route('/api/colours/overrides')
def list_overrides():
    """List all saved colour overrides."""
    overrides = ColourOverride.query.order_by(ColourOverride.horse_name).all()
    return jsonify([{
        'horse_name': o.horse_name,
        'colour':     o.colour,
        'updated_at': o.updated_at
    } for o in overrides])


@app.route('/api/colours/override/<horse_name>', methods=['DELETE'])
def delete_override(horse_name):
    """Remove a manual colour override."""
    override = ColourOverride.query.filter_by(horse_name=horse_name).first()
    if override:
        db.session.delete(override)
        db.session.commit()
    return jsonify({'status': 'ok'})


def build_race_obj(r_data):
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
            'odds':    r.odds
        } for r in r_data['runners']]
    }


def sort_by_meeting(runners):
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
        meeting_obj = {
            'meeting': m_data['meeting'].name,
            'date':    m_data['meeting'].date,
            'races':   []
        }
        for r_id, r_data in m_data['races'].items():
            meeting_obj['races'].append(build_race_obj(r_data))
        meeting_obj['races'].sort(key=lambda x: x['time'])
        result.append(meeting_obj)
    return result


def sort_by_time(runners):
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
            result.append({
                'meeting': race.meeting.name,
                'date':    race.meeting.date,
                'races':   [build_race_obj(r_data)]
            })
    return result


@app.route('/api/sync', methods=['POST'])
def manual_sync():
    sync_todays_races(app)
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    app.run(debug=True, port=5001)
