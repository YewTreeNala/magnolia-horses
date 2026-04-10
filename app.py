from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from models import db, Meeting, Race, Runner
from sync import sync_todays_races
from rapidfuzz import fuzz
import jellyfish
import os

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///racing.db'
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
