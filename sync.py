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


def _parse_results(res_json):
    """
    Build lookup: "course_lower_time" -> {horse_name_lower: result_dict}
    Both APIs use the same 12hr time format e.g. "1:50", "2:20" - no conversion needed.
    """
    results_by_key = {}
    for race in res_json.get("results", []):
        course = (race.get("course") or "").strip().lower()
        off    = (race.get("off") or "").strip()
        key    = f"{course}_{off}"
        runners = {}
        for r in race.get("runners", []):
            horse = (r.get("horse") or "").strip().lower()
            runners[horse] = {
                "position": str(r.get("position") or ""),
                "sp":       r.get("sp") or "",
                "sp_dec":   str(r.get("sp_dec") or ""),
                "btn":      str(r.get("btn") or ""),
            }
        results_by_key[key] = runners
    return results_by_key


def sync_todays_races(app):
    with app.app_context():

        # ── Fetch racecards (basic plan) ──────────────────────────────────────
        rc_resp = requests.get(f"{BASE_URL}/racecards/basic", auth=get_auth())
        if rc_resp.status_code != 200:
            print(f"Racecard API error: {rc_resp.status_code} — {rc_resp.text[:200]}")
            return

        # ── Fetch results ─────────────────────────────────────────────────────
        results_by_key = {}
        res_resp = requests.get(f"{BASE_URL}/results/today", auth=get_auth())
        if res_resp.status_code == 200:
            results_by_key = _parse_results(res_resp.json())
            n = len(set(k.rsplit("_", 1)[0] for k in results_by_key))
            print(f"Results loaded: {n} finished races")
        else:
            print(f"Results API: {res_resp.status_code} — continuing without results")

        # ── Load colour overrides ─────────────────────────────────────────────
        overrides = {
            o.horse_name.lower(): o.colour
            for o in ColourOverride.query.all()
        }

        # ── Existing meetings lookup ──────────────────────────────────────────
        existing_meetings = {}
        for m in Meeting.query.all():
            existing_meetings[f"{m.name.lower()}_{m.date}"] = m

        seen_keys = set()

        for racecard in rc_resp.json().get("racecards", []):
            course         = racecard.get("course", "")
            date_str       = racecard.get("date", "")
            race_status    = racecard.get("race_status", "")
            off_time       = racecard.get("off_time", "")
            race_name      = racecard.get("race_name", "")
            going_detailed = racecard.get("going_detailed", "") or racecard.get("going", "")
            weather        = racecard.get("weather", "") or ""
            m_key          = f"{course.lower()}_{date_str}"

            # Get or create meeting
            if m_key in existing_meetings:
                meeting = existing_meetings[m_key]
            else:
                meeting = Meeting(name=course, date=date_str, course=course)
                db.session.add(meeting)
                db.session.flush()
                existing_meetings[m_key] = meeting
            seen_keys.add(m_key)

            # Find or create race
            race = None
            for er in meeting.races:
                if er.time == off_time and er.name == race_name:
                    race = er
                    break

            if race is None:
                race = Race(
                    meeting_id=meeting.id,
                    time=off_time,
                    name=race_name,
                    distance=str(racecard.get("distance_f", "")),
                    race_class=racecard.get("race_class", ""),
                    prize=racecard.get("prize", ""),
                    race_status=race_status,
                    going_detailed=going_detailed,
                    weather=weather
                )
                db.session.add(race)
                db.session.flush()
            else:
                race.race_status    = race_status
                race.going_detailed = going_detailed
                race.weather        = weather

            # Results lookup - both APIs use same 12hr time format
            result_runners = results_by_key.get(f"{course.lower()}_{off_time}", {})


            existing_runners = {r.horse_name.lower(): r for r in race.runners}

            for r in racecard.get("runners", []):
                horse_name = r.get("horse") or ""
                horse_key  = horse_name.lower()

                colour = overrides.get(horse_key) or expand_colour(r.get("colour") or "")

                # Result data
                result   = result_runners.get(horse_key, {})
                position = result.get("position", "")
                sp_dec   = result.get("sp_dec", "") or str(r.get("sp_dec") or "")

                # Trainer 14-day form
                t14 = r.get("trainer_14_days") or {}
                if isinstance(t14, dict) and t14.get("runs"):
                    trainer_14 = f"{t14.get('wins','0')}/{t14.get('runs','0')}"
                else:
                    trainer_14 = ""

                # Wind surgery
                wind = ""
                if r.get("wind_surgery") == "1" or r.get("wind_surgery") is True:
                    wind = "1"

                fields = {
                    "colour":          colour,
                    "draw":            str(r.get("draw") or ""),
                    "age":             str(r.get("age") or ""),
                    "sex":             r.get("sex") or "",
                    "trainer":         r.get("trainer") or "",
                    "jockey":          r.get("jockey") or "",
                    "owner":           r.get("owner") or "",
                    "form":            r.get("form") or "",
                    "weight":          str(r.get("lbs") or ""),
                    "official_rating": str(r.get("ofr") or ""),
                    "rpr":             str(r.get("rpr") or ""),
                    "ts":              str(r.get("ts") or ""),
                    "odds":            sp_dec,
                    "headgear":        r.get("headgear") or "",
                    "headgear_run":    str(r.get("headgear_run") or ""),
                    "last_run":        str(r.get("last_run") or ""),
                    "position":        position,
                    "silk_url":        r.get("silk_url") or "",
                    "spotlight":       r.get("spotlight") or "",
                    "comment":         r.get("comment") or "",
                    "wind_surgery":    wind,
                    "trainer_14_days": trainer_14,
                }

                if horse_key in existing_runners:
                    runner = existing_runners[horse_key]
                    for k, v in fields.items():
                        setattr(runner, k, v)
                else:
                    runner = Runner(
                        race_id=race.id,
                        horse_name=horse_name,
                        number=str(r.get("number") or ""),
                        **fields
                    )
                    db.session.add(runner)

        # Remove stale meetings
        for key, meeting in list(existing_meetings.items()):
            if key not in seen_keys:
                db.session.delete(meeting)

        db.session.commit()
        print(f"Sync complete — {len(seen_keys)} meetings at {datetime.now().strftime('%H:%M:%S')}")
