import requests
import os
from datetime import datetime
from models import db, Meeting, Race, Runner, ColourOverride

BASE_URL = "https://api.theracingapi.com/v1"


def get_auth():
    return (os.getenv("RACING_API_USER"), os.getenv("RACING_API_KEY"))


def expand_colour(code):
    if not code:
        return ""
    mapping = {
        "b":     "Bay",
        "br":    "Dark bay/brown",
        "ch":    "Chestnut",
        "gr":    "Grey",
        "bl":    "Black",
        "ro":    "Roan",
        "b/br":  "Dark bay/brown",
        "gr/b":  "Grey",
        "b/bl":  "Black",
        "gr/ro": "Roan",
        "b/ro":  "Roan",
    }
    return mapping.get(code.lower().strip(), code.capitalize())


def sync_todays_races(app):
    with app.app_context():
        response = requests.get(
            f"{BASE_URL}/racecards/free",
            auth=get_auth()
        )
        if response.status_code != 200:
            print(f"API error: {response.status_code} — {response.text[:200]}")
            return

        data = response.json()

        # Load all colour overrides for fast lookup
        overrides = {
            o.horse_name.lower(): o.colour
            for o in ColourOverride.query.all()
        }

        # Build a map of existing races so we can update in place
        # rather than deleting everything (preserves any data we add later)
        existing_meetings = {}
        for m in Meeting.query.all():
            existing_meetings[f"{m.name}_{m.date}"] = m

        existing_races = {}
        for r in Race.query.all():
            existing_races[r.id] = r

        # Track which meetings/races we saw this sync
        seen_meeting_keys = set()
        seen_race_ids     = set()

        for racecard in data.get("racecards", []):
            course      = racecard.get("course", "")
            date        = racecard.get("date", "")
            race_status = racecard.get("race_status", "")
            key         = f"{course}_{date}"

            # Get or create meeting
            if key in existing_meetings:
                meeting = existing_meetings[key]
            else:
                meeting = Meeting(name=course, date=date, course=course)
                db.session.add(meeting)
                db.session.flush()
                existing_meetings[key] = meeting
            seen_meeting_keys.add(key)

            # Find existing race by meeting + time + name, or create
            off_time   = racecard.get("off_time", "")
            race_name  = racecard.get("race_name", "")
            race       = None
            for existing_race in meeting.races:
                if existing_race.time == off_time and existing_race.name == race_name:
                    race = existing_race
                    break

            if race is None:
                race = Race(
                    meeting_id=meeting.id,
                    time=off_time,
                    name=race_name,
                    distance=str(racecard.get("distance_f", "")),
                    race_class=racecard.get("race_class", ""),
                    prize=racecard.get("prize", ""),
                    race_status=race_status
                )
                db.session.add(race)
                db.session.flush()
            else:
                # Update status (race may have finished since last sync)
                race.race_status = race_status

            seen_race_ids.add(race.id)

            # Build map of existing runners for this race
            existing_runners = {r.horse_name.lower(): r for r in race.runners}

            for r in racecard.get("runners", []):
                horse_name = r.get("horse") or ""

                if horse_name.lower() in overrides:
                    colour = overrides[horse_name.lower()]
                else:
                    colour = expand_colour(r.get("colour") or "")

                position = str(r.get("position") or r.get("finishing_position") or "")
                headgear = r.get("headgear") or ""
                last_run = str(r.get("last_run") or "")

                if horse_name.lower() in existing_runners:
                    # Update existing runner
                    runner = existing_runners[horse_name.lower()]
                    runner.colour          = colour
                    runner.jockey          = r.get("jockey") or ""
                    runner.weight          = str(r.get("lbs") or "")
                    runner.official_rating = str(r.get("ofr") or "")
                    runner.odds            = str(r.get("sp_dec") or "")
                    runner.headgear        = headgear
                    runner.last_run        = last_run
                    runner.position        = position
                else:
                    # Create new runner
                    runner = Runner(
                        race_id=race.id,
                        horse_name=horse_name,
                        number=str(r.get("number") or ""),
                        colour=colour,
                        age=str(r.get("age") or ""),
                        sex=r.get("sex") or "",
                        trainer=r.get("trainer") or "",
                        jockey=r.get("jockey") or "",
                        owner=r.get("owner") or "",
                        form=r.get("form") or "",
                        weight=str(r.get("lbs") or ""),
                        official_rating=str(r.get("ofr") or ""),
                        odds=str(r.get("sp_dec") or ""),
                        headgear=headgear,
                        last_run=last_run,
                        position=position
                    )
                    db.session.add(runner)

        # Remove meetings/races not seen in this sync
        for key, meeting in list(existing_meetings.items()):
            if key not in seen_meeting_keys:
                db.session.delete(meeting)

        db.session.commit()
        print(f"Sync complete — {len(seen_meeting_keys)} meetings at {datetime.now().strftime('%H:%M:%S')}")
