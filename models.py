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
    searches   = db.relationship('SavedSearch', backref='user', lazy=True)

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


class SavedSearch(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name       = db.Column(db.String(100), nullable=False)
    filters    = db.Column(db.Text, nullable=False)
    alert      = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(30))


class Meeting(db.Model):
    id     = db.Column(db.Integer, primary_key=True)
    name   = db.Column(db.String(100))
    date   = db.Column(db.String(20))
    course = db.Column(db.String(100))
    races  = db.relationship('Race', backref='meeting', lazy=True)


class Race(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    meeting_id     = db.Column(db.Integer, db.ForeignKey('meeting.id'))
    time           = db.Column(db.String(10))
    name           = db.Column(db.Text)
    distance       = db.Column(db.String(20))
    race_class     = db.Column(db.String(50))
    prize          = db.Column(db.String(50))
    race_status    = db.Column(db.String(20),  default='')
    going_detailed = db.Column(db.Text,        default='')
    weather        = db.Column(db.Text,        default='')
    runners        = db.relationship('Runner', backref='race', lazy=True)


class Runner(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    race_id         = db.Column(db.Integer, db.ForeignKey('race.id'))
    horse_id        = db.Column(db.String(30),  default='')
    horse_name      = db.Column(db.String(100))
    number          = db.Column(db.String(20))
    draw            = db.Column(db.String(20),  default='')
    colour          = db.Column(db.String(30))
    age             = db.Column(db.String(10))
    sex             = db.Column(db.String(20))
    trainer         = db.Column(db.String(100))
    jockey          = db.Column(db.String(100))
    owner           = db.Column(db.String(100))
    form            = db.Column(db.String(30))
    weight          = db.Column(db.String(20))
    official_rating = db.Column(db.String(20))
    rpr             = db.Column(db.String(20),  default='')
    ts              = db.Column(db.String(20),  default='')
    odds            = db.Column(db.String(20))
    headgear        = db.Column(db.String(20),  default='')
    headgear_run    = db.Column(db.String(20),  default='')
    last_run        = db.Column(db.String(20),  default='')
    position        = db.Column(db.String(20),  default='')
    silk_url        = db.Column(db.Text,        default='')
    spotlight       = db.Column(db.Text,        default='')
    comment         = db.Column(db.Text,        default='')
    wind_surgery    = db.Column(db.String(10),  default='')
    trainer_14_days = db.Column(db.String(20),  default='')


class HorseProfile(db.Model):
    """One row per unique horse — keyed by API horse_id."""
    __tablename__ = 'horse_profile'
    horse_id   = db.Column(db.String(30), primary_key=True)
    name       = db.Column(db.String(100))
    colour     = db.Column(db.String(30))
    sex        = db.Column(db.String(20))
    dob        = db.Column(db.String(20))
    region     = db.Column(db.String(10))
    sire       = db.Column(db.String(100))
    dam        = db.Column(db.String(100))
    trainer    = db.Column(db.String(100))
    owner      = db.Column(db.String(100))
    updated_at = db.Column(db.String(30))
    runs       = db.relationship('HorseRun', backref='horse', lazy=True)


class HorseRun(db.Model):
    """One row per historical run for a horse."""
    __tablename__ = 'horse_run'
    id            = db.Column(db.Integer, primary_key=True)
    horse_id      = db.Column(db.String(30), db.ForeignKey('horse_profile.horse_id'), nullable=False)
    race_id       = db.Column(db.String(30), unique=False)   # API race_id e.g. rac_XXXXXXXX
    date          = db.Column(db.String(20))
    course        = db.Column(db.String(100))
    race_name     = db.Column(db.Text)
    race_type     = db.Column(db.String(20))
    race_class    = db.Column(db.String(20))
    pattern       = db.Column(db.String(30))
    dist          = db.Column(db.String(20))
    going         = db.Column(db.String(50))
    surface       = db.Column(db.String(20))
    position      = db.Column(db.String(10))
    sp            = db.Column(db.String(20))
    sp_dec        = db.Column(db.String(20))
    jockey        = db.Column(db.String(100))
    trainer       = db.Column(db.String(100))
    weight        = db.Column(db.String(20))
    btn           = db.Column(db.String(20))
    ovr_btn       = db.Column(db.String(20))
    official_rating = db.Column(db.String(10))
    prize         = db.Column(db.String(20))
    comment       = db.Column(db.Text)
    field         = db.relationship('HorseRunField', backref='run', lazy=True,
                                    cascade='all, delete-orphan')
    __table_args__ = (db.UniqueConstraint('horse_id', 'race_id', name='uq_horse_race'),)


class HorseRunField(db.Model):
    """Every runner in a race stored against a HorseRun."""
    __tablename__ = 'horse_run_field'
    id         = db.Column(db.Integer, primary_key=True)
    run_id     = db.Column(db.Integer, db.ForeignKey('horse_run.id'), nullable=False)
    horse_id   = db.Column(db.String(30))
    horse_name = db.Column(db.String(100))
    position   = db.Column(db.String(10))
    sp         = db.Column(db.String(20))
    sp_dec     = db.Column(db.String(20))
    jockey     = db.Column(db.String(100))
    trainer    = db.Column(db.String(100))
    weight     = db.Column(db.String(20))
    btn        = db.Column(db.String(20))
    official_rating = db.Column(db.String(10))
    silk_url   = db.Column(db.Text)


class ColourOverride(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    horse_name = db.Column(db.String(100), unique=True, nullable=False)
    colour     = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30))


class EmailLog(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject   = db.Column(db.String(200))
    html_body = db.Column(db.Text)
    status    = db.Column(db.String(20))
    sent_at   = db.Column(db.String(30))
    user      = db.relationship('User', backref=db.backref('email_logs', lazy=True))


class SyncLog(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.String(30))
    level      = db.Column(db.String(10))
    message    = db.Column(db.Text)
