from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Meeting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    date = db.Column(db.String(20))
    course = db.Column(db.String(100))
    races = db.relationship('Race', backref='meeting', lazy=True)

class Race(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey('meeting.id'))
    time = db.Column(db.String(10))
    name = db.Column(db.String(200))
    distance = db.Column(db.String(20))
    race_class = db.Column(db.String(50))
    prize = db.Column(db.String(50))
    runners = db.relationship('Runner', backref='race', lazy=True)

class Runner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    race_id = db.Column(db.Integer, db.ForeignKey('race.id'))
    horse_name = db.Column(db.String(100))
    number = db.Column(db.Integer)
    colour = db.Column(db.String(30))
    age = db.Column(db.String(5))
    sex = db.Column(db.String(5))
    trainer = db.Column(db.String(100))
    jockey = db.Column(db.String(100))
    owner = db.Column(db.String(100))
    form = db.Column(db.String(30))
    weight = db.Column(db.String(10))
    official_rating = db.Column(db.String(10))
    odds = db.Column(db.String(20))