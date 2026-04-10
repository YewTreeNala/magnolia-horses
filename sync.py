import requests
import os

def expand_colour(code):
    mapping = {
        "b":    "Bay",
        "br":   "Dark bay/brown",
        "ch":   "Chestnut",
        "gr":   "Grey",
        "bl":   "Black",
        "ro":   "Roan",
        "b/br": "Dark bay/brown",
        "gr/b": "Grey",
        "b/bl": "Black",
    }
    return mapping.get(code.lower().strip(), code)

from models import db, Meeting, Race, Runner

BASE_URL = "https://api.theracingapi.com/v1"

def get_auth():
    return (os.getenv("RACING_API_USER"), os.getenv("RACING_API_KEY"))

def sync_todays_races(app):
    with app.app_context():
        response = requests.get(
            f"{BASE_URL}/racecards/free",
            auth=get_auth()
        )
        if response.status_code != 200:
            print(f"API error: {response.status_code}")
            return

        data = response.json()

        # Clear existing data
        Runner.query.delete()
        Race.query.delete()
        Meeting.query.delete()
        db.session.commit()

        # Free tier returns one flat entry per race — group by course+date
        meetings = {}
        for racecard in data.get("racecards", []):
            course = racecard.get("course", "")
            date   = racecard.get("date", "")
            key    = f"{course}_{date}"

            if key not in meetings:
                meeting = Meeting(
                    name=course,
                    date=date,
                    course=course
                )
                db.session.add(meeting)
                db.session.flush()
                meetings[key] = meeting

            meeting = meetings[key]

            race = Race(
                meeting_id=meeting.id,
                time=racecard.get("off_time", ""),
                name=racecard.get("race_name", ""),
                distance=str(racecard.get("distance_f", "")),
                race_class=racecard.get("race_class", ""),
                prize=racecard.get("prize", "")
            )
            db.session.add(race)
            db.session.flush()

            for r in racecard.get("runners", []):
                runner = Runner(
                    race_id=race.id,
                    horse_name=r.get("horse", ""),
                    number=r.get("number", 0),
                    colour=expand_colour(r.get("colour", "")),
                    age=str(r.get("age", "")),
                    sex=r.get("sex", ""),
                    trainer=r.get("trainer", ""),
                    jockey=r.get("jockey", ""),
                    owner=r.get("owner", ""),
                    form=r.get("form", ""),
                    weight=str(r.get("lbs", "")),
                    official_rating=str(r.get("ofr", "")),
                    odds=str(r.get("sp_dec", ""))
                )
                db.session.add(runner)

        db.session.commit()
        print("Sync complete")