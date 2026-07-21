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
    name           = db.Column(db.String(200))
    distance       = db.Column(db.String(20))
    race_class     = db.Column(db.String(50))
    prize          = db.Column(db.String(50))
    race_status    = db.Column(db.String(20),  default='')
    going_detailed = db.Column(db.String(100), default='')
    weather        = db.Column(db.String(100), default='')
    runners        = db.relationship('Runner', backref='race', lazy=True)


class Runner(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    race_id         = db.Column(db.Integer, db.ForeignKey('race.id'))
    horse_name      = db.Column(db.String(100))
    number          = db.Column(db.String(10))
    draw            = db.Column(db.String(10),  default='')
    colour          = db.Column(db.String(30))
    age             = db.Column(db.String(5))
    sex             = db.Column(db.String(10))
    trainer         = db.Column(db.String(100))
    jockey          = db.Column(db.String(100))
    owner           = db.Column(db.String(100))
    form            = db.Column(db.String(30))
    weight          = db.Column(db.String(10))
    official_rating = db.Column(db.String(10))
    rpr             = db.Column(db.String(10),  default='')
    ts              = db.Column(db.String(10),  default='')
    odds            = db.Column(db.String(20))
    headgear        = db.Column(db.String(20),  default='')
    headgear_run    = db.Column(db.String(10),  default='')
    last_run        = db.Column(db.String(10),  default='')
    position        = db.Column(db.String(5),   default='')
    silk_url        = db.Column(db.String(300), default='')
    spotlight       = db.Column(db.Text,        default='')
    comment         = db.Column(db.Text,        default='')
    wind_surgery    = db.Column(db.String(5),   default='')
    trainer_14_days = db.Column(db.String(50),  default='')



class Tipster(db.Model):
    """One row per tipster source."""
    __tablename__ = 'tipster'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.String(30))
    tips       = db.relationship('Tip', backref='tipster', lazy=True)


class Tip(db.Model):
    """One row per horse tip from a tipster."""
    __tablename__ = 'tip'
    id            = db.Column(db.Integer, primary_key=True)
    tipster_id    = db.Column(db.Integer, db.ForeignKey('tipster.id'), nullable=False)
    horse_name    = db.Column(db.String(100), nullable=False)
    horse_id      = db.Column(db.String(30),  default='')   # FK to HorseProfile if found
    tip_date      = db.Column(db.String(20))                # date of the tip message
    tip_datetime  = db.Column(db.String(30))                # full datetime
    course        = db.Column(db.String(100), default='')
    race_time     = db.Column(db.String(10),  default='')
    race_date     = db.Column(db.String(20),  default='')   # date the race runs
    bet_type      = db.Column(db.String(10),  default='ew') # 'win' or 'ew'
    stake_pts     = db.Column(db.Float,       default=0.5)
    odds          = db.Column(db.String(20),  default='')   # fractional e.g. "8/1"
    odds_dec      = db.Column(db.Float,       default=0.0)  # decimal equivalent
    each_way_places = db.Column(db.Integer,   default=0)    # 0 = win only
    each_way_fraction = db.Column(db.Integer, default=5)    # denominator e.g. 5 = 1/5
    reasoning     = db.Column(db.Text,        default='')
    raw_message   = db.Column(db.Text,        default='')
    telegram_msg_id = db.Column(db.Integer,   default=0)
    uncertain     = db.Column(db.Boolean,     default=False) # flagged for review
    settled       = db.Column(db.Boolean,     default=False)
    created_at    = db.Column(db.String(30))
    result        = db.relationship('TipResult', backref='tip', lazy=True,
                                    uselist=False, cascade='all, delete-orphan')


class TipResult(db.Model):
    """Settlement result for a tip -- auto-populated from race sync."""
    __tablename__ = 'tip_result'
    id            = db.Column(db.Integer, primary_key=True)
    tip_id        = db.Column(db.Integer, db.ForeignKey('tip.id'), nullable=False)
    position      = db.Column(db.String(10),  default='')
    sp            = db.Column(db.String(20),  default='')
    sp_dec        = db.Column(db.Float,       default=0.0)
    result_type   = db.Column(db.String(10),  default='')  # 'win', 'place', 'loss', 'void'
    win_pts       = db.Column(db.Float,       default=0.0) # P&L on win part
    place_pts     = db.Column(db.Float,       default=0.0) # P&L on place part (EW only)
    total_pts     = db.Column(db.Float,       default=0.0) # combined P&L
    settled_at    = db.Column(db.String(30))


class HorseProfile(db.Model):
    """One row per unique horse � keyed by API horse_id."""
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
    race_id       = db.Column(db.String(30))
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
    level      = db.Column(db.String(10))  # INFO, WARN, ERROR
    message    = db.Column(db.Text)
