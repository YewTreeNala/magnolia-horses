from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(150), unique=True, nullable=False)
    password   = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.String(30))
    tagged     = db.relationship('TaggedHorse', backref='user', lazy=True)

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)


class TaggedHorse(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    horse_name = db.Column(db.String(100), nullable=False)
    notes      = db.Column(db.Text, default='')
    tagged_at  = db.Column(db.String(30))
    __table_args__ = (db.UniqueConstraint('user_id', 'horse_name'),)


class Meeting(db.Model):
    id     = db.Column(db.Integer, primary_key=True)
    name   = db.Column(db.String(100))
    date   = db.Column(db.String(20))
    course = db.Column(db.String(100))
    races  = db.relationship('Race', backref='meeting', lazy=True)


class Race(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey('meeting.id'))
    time       = db.Column(db.String(10))
    name       = db.Column(db.String(200))
    distance   = db.Column(db.String(20))
    race_class = db.Column(db.String(50))
    prize      = db.Column(db.String(50))
    runners    = db.relationship('Runner', backref='race', lazy=True)


class Runner(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    race_id         = db.Column(db.Integer, db.ForeignKey('race.id'))
    horse_name      = db.Column(db.String(100))
    number          = db.Column(db.String(10))
    colour          = db.Column(db.String(30))
    age             = db.Column(db.String(5))
    sex             = db.Column(db.String(10))
    trainer         = db.Column(db.String(100))
    jockey          = db.Column(db.String(100))
    owner           = db.Column(db.String(100))
    form            = db.Column(db.String(30))
    weight          = db.Column(db.String(10))
    official_rating = db.Column(db.String(10))
    odds            = db.Column(db.String(20))


class ColourOverride(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    horse_name = db.Column(db.String(100), unique=True, nullable=False)
    colour     = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30))
