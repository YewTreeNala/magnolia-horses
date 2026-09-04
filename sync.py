from utils import strip_country, is_uk_course
# deployed: 20260723_1456
import re
import time
import requests
import os
from datetime import datetime
from models import db, Meeting, Race, Runner, RunnerHistory, ColourOverride, SyncLog, HorseProfile, HorseRun, HorseRunField

BASE_URL = "https://api.theracingapi.com/v1"

# Delay between per-horse history requests. The Racing API rate
# limits sustained bursts; without this the nightly job got ~50
# horses through and failed the remaining several hundred.
REQUEST_DELAY_SECONDS = 0.5


def get_auth():
    return (os.getenv("RACING_API_USER"), os.getenv("RACING_API_KEY"))


def _log(level, message):
    try:
        entry = SyncLog(
            created_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            level=level,
            message=message
        )
        db.session.add(entry)
        db.session.flush()
    except Exception as e:
        print(f"Log write failed: {e}")



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
    results_by_key = {}
    for race in res_json.get("results", []):
        course = strip_country((race.get("course") or "").strip()).lower()
        off    = (race.get("off") or "").strip()
        key    = f"{course}_{off}"
        runners = {}
        for r in race.get("runners", []):
            raw_horse = (r.get("horse") or "").strip()
            horse     = strip_country(raw_horse).lower()
            runners[horse] = {
                "position": str(r.get("position") or ""),
                "sp":       r.get("sp") or "",
                "sp_dec":   str(r.get("sp_dec") or ""),
                "btn":      str(r.get("btn") or ""),
            }
        results_by_key[key] = runners
    return results_by_key



def archive_to_runner_history(app):
    """Copy today's results into RunnerHistory for permanent storage."""
    with app.app_context():
        from datetime import date as _date
        today = _date.today().strftime('%Y-%m-%d')
        meetings = Meeting.query.filter_by(date=today).all()
        added = 0
        for meeting in meetings:
            for race in meeting.races:
                for runner in race.runners:
                    existing = RunnerHistory.query.filter_by(
                        horse_name=runner.horse_name,
                        race_date=today,
                        course=meeting.name,
                        race_time=race.time
                    ).first()
                    if existing:
                        # Update position/SP if now available
                        if runner.position:
                            existing.position = runner.position
                        if runner.sp:
                            existing.sp = runner.sp
                        if runner.odds:
                            existing.odds = runner.odds
                    else:
                        rh = RunnerHistory(
                            race_date       = today,
                            course          = meeting.name or '',
                            race_time       = race.time or '',
                            race_name       = race.name or '',
                            race_class      = race.race_class or '',
                            distance        = race.distance or '',
                            going           = race.going_detailed or '',
                            horse_id        = runner.horse_id or '',
                            horse_name      = runner.horse_name or '',
                            number          = runner.number or '',
                            draw            = runner.draw or '',
                            colour          = runner.colour or '',
                            age             = runner.age or '',
                            sex             = runner.sex or '',
                            trainer         = runner.trainer or '',
                            jockey          = runner.jockey or '',
                            owner           = runner.owner or '',
                            form            = runner.form or '',
                            weight          = runner.weight or '',
                            official_rating = runner.official_rating or '',
                            rpr             = runner.rpr or '',
                            ts              = runner.ts or '',
                            odds            = runner.odds or '',
                            sp              = runner.sp or '',
                            headgear        = runner.headgear or '',
                            last_run        = runner.last_run or '',
                            position        = runner.position or '',
                            silk_url        = runner.silk_url or '',
                            wind_surgery    = runner.wind_surgery or '',
                            trainer_14_days = runner.trainer_14_days or '',
                        )
                        db.session.add(rh)
                        added += 1
        db.session.commit()
        _log('INFO', f'RunnerHistory: archived {added} runners for {today}')
        return added



def update_horse_ids_from_runners(app):
    """Update tip.horse_id and runner_history.horse_id from today's racecard."""
    with app.app_context():
        import re as _re
        def _strip(name):
            return _re.sub(r'\s*\([A-Z]+\)\s*$', '', name or '').strip().lower()

        from models import Runner, Tip, RunnerHistory
        id_map = {}
        for r in db.session.query(Runner).all():
            if r.horse_id and r.horse_name:
                id_map[_strip(r.horse_name)] = r.horse_id

        if not id_map:
            return 0

        updated = 0
        for tip in Tip.query.filter(Tip.horse_id == '').all():
            hid = id_map.get(_strip(tip.horse_name))
            if hid:
                tip.horse_id = hid
                updated += 1

        for rh in RunnerHistory.query.filter(RunnerHistory.horse_id == '').all():
            hid = id_map.get(_strip(rh.horse_name))
            if hid:
                rh.horse_id = hid
                updated += 1

        if updated:
            db.session.commit()
            _log('INFO', f'horse_id updated for {updated} tip/history records')
        return updated


def sync_todays_races(app):
    with app.app_context():

        _log("INFO", "Sync started")

        # ── Fetch racecards ───────────────────────────────────────────────────
        rc_resp = requests.get(f"{BASE_URL}/racecards/basic", auth=get_auth())
        if rc_resp.status_code != 200:
            _log("ERROR", f"Racecard API error: {rc_resp.status_code} — {rc_resp.text[:200]}")
            db.session.commit()
            return
        racecards = rc_resp.json().get("racecards", [])
        _log("INFO", f"Racecards fetched: {len(racecards)}")

        # ── Fetch results ─────────────────────────────────────────────────────
        results_by_key = {}
        res_resp = requests.get(f"{BASE_URL}/results/today", auth=get_auth())
        if res_resp.status_code == 200:
            results_by_key = _parse_results(res_resp.json())
            _log("INFO", f"Results fetched: {len(results_by_key)} race keys")
        else:
            _log("ERROR", f"Results API failed: {res_resp.status_code}")

        # ── Load colour overrides ─────────────────────────────────────────────
        overrides = {
            o.horse_name.lower(): o.colour
            for o in ColourOverride.query.all()
        }

        # ── Existing meetings lookup ──────────────────────────────────────────
        existing_meetings = {}
        for m in Meeting.query.order_by(Meeting.id).all():
            key = f"{m.name.lower()}_{m.date}"
            if key not in existing_meetings:  # keep lowest ID
                existing_meetings[key] = m

        seen_keys     = set()
        result_writes = 0

        for racecard in racecards:
            course         = (racecard.get("course") or "").strip()
            date_str       = racecard.get("date", "")
            race_status    = racecard.get("race_status", "")
            off_time       = (racecard.get("off_time") or "").strip()
            race_name      = racecard.get("race_name", "")
            going_detailed = racecard.get("going_detailed", "") or racecard.get("going", "")
            weather        = racecard.get("weather", "") or ""
            m_key          = f"{course.lower()}_{date_str}"

            if m_key in existing_meetings:
                meeting = existing_meetings[m_key]
            else:
                meeting = Meeting(name=course, date=date_str, course=course)
                db.session.add(meeting)
                db.session.flush()
                existing_meetings[m_key] = meeting
            seen_keys.add(m_key)

            # Query races directly from DB to avoid stale relationship cache
            race = Race.query.filter_by(
                meeting_id=meeting.id,
                time=off_time,
                name=race_name
            ).order_by(Race.id).first()

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

            result_key     = f"{strip_country(course).strip().lower()}_{off_time.strip()}"
            result_runners = results_by_key.get(result_key, {})

            # Query DB directly to avoid stale in-memory state from concurrent syncs
            existing_runners = {r.horse_name.lower(): r for r in Runner.query.filter_by(race_id=race.id).order_by(Runner.id).all()}

            for r in racecard.get("runners", []):
                horse_name         = r.get("horse") or ""
                horse_key          = horse_name.strip().lower()
                horse_key_stripped = strip_country(horse_name).strip().lower()
                horse_id           = r.get("horse_id") or ""

                colour = overrides.get(horse_key) or expand_colour(r.get("colour") or "")

                result   = result_runners.get(horse_key_stripped, {})
                position = result.get("position", "")
                sp_frac  = result.get("sp", "")
                sp_dec   = result.get("sp_dec", "") or str(r.get("sp_dec") or "")

                t14 = r.get("trainer_14_days") or {}
                if isinstance(t14, dict) and t14.get("runs"):
                    trainer_14 = f"{t14.get('wins','0')}/{t14.get('runs','0')}"
                else:
                    trainer_14 = ""

                wind = ""
                if r.get("wind_surgery") == "1" or r.get("wind_surgery") is True:
                    wind = "1"

                fields = {
                    "horse_id":        horse_id,
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
                    "headgear":        r.get("headgear") or "",
                    "headgear_run":    str(r.get("headgear_run") or ""),
                    "last_run":        str(r.get("last_run") or ""),
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
                    if position:
                        runner.position = position
                        result_writes += 1
                        # Immediately update RunnerHistory so positions are captured
                        try:
                            rh_exist = RunnerHistory.query.filter_by(
                                horse_name=horse_name,
                                race_date=date_str,
                                course=course,
                                race_time=off_time
                            ).first()
                            if rh_exist:
                                rh_exist.position = position
                                if sp_frac: rh_exist.sp = sp_frac
                                if sp_dec: rh_exist.odds = str(sp_dec)
                        except Exception:
                            pass
                    if sp_frac:
                        runner.sp = sp_frac
                    if sp_dec:
                        runner.odds = sp_dec
                else:
                    runner = Runner(
                        race_id=race.id,
                        horse_name=horse_name,
                        number=str(r.get("number") or ""),
                        position=position,
                        sp=sp_frac,
                        odds=sp_dec,
                        **fields
                    )
                    db.session.add(runner)
                    if position:
                        result_writes += 1

        # Remove stale meetings
        for key, meeting in list(existing_meetings.items()):
            if key not in seen_keys:
                db.session.delete(meeting)

        _log("INFO", f"Sync complete — {len(seen_keys)} meetings, {result_writes} position writes")
        db.session.commit()



def _fetch_horse_results(horse_id):
    """Fetch a horse's career results.

    /racecards/{id}/results is racecard-scoped and 422s for any horse not
    in today's or tomorrow's cards ("horse ... not in today or tomorrow's
    racecards"), which is why the 23:00 job failed ~86% of the time and
    why historical horses could never be backfilled.

    /horses/{id}/results is the general horse results endpoint. Falls
    back to the old path if it isn't available on the current plan, so
    today's runners keep working either way.

    Returns (response, endpoint_used)."""
    for attempt in range(4):
        resp = requests.get(f"{BASE_URL}/horses/{horse_id}/results",
                            auth=get_auth(), timeout=15)
        if resp.status_code == 429:
            wait = float(resp.headers.get('Retry-After') or (2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        break

    if resp.status_code == 200:
        return resp, 'horses'

    # 401/403/404 all indicate the endpoint isn't usable on this plan
    # (401 is what The Racing API returns for an endpoint outside your
    # subscription, not a credentials problem - the same auth works for
    # the racecards path). Fall back rather than failing the horse.
    if resp.status_code in (401, 403, 404):
        for attempt in range(4):
            fb = requests.get(f"{BASE_URL}/racecards/{horse_id}/results",
                              auth=get_auth(), timeout=15)
            if fb.status_code == 429:
                wait = float(fb.headers.get('Retry-After') or (2 ** attempt))
                time.sleep(min(wait, 30))
                continue
            break
        return fb, 'racecards-fallback'

    return resp, 'horses'


def sync_horse_history(app):
    """End-of-day job: fetch and store full race history for every horse seen today."""
    with app.app_context():
        _log("INFO", "Horse history sync started")

        from datetime import date
        today = date.today().strftime('%Y-%m-%d')

        # Collect all unique horse_ids from today's runners
        rows = db.session.query(Runner.horse_id, Runner.horse_name,
                                Runner.colour, Runner.age, Runner.sex,
                                Runner.trainer, Runner.owner)\
            .join(Race).join(Meeting)\
            .filter(Meeting.date == today)\
            .filter(Runner.horse_id != '')\
            .all()

        seen = {}
        for row in rows:
            if row.horse_id not in seen:
                seen[row.horse_id] = row

        _log("INFO", f"Horse history: {len(seen)} unique horses to process")

        fetched = 0
        errors  = 0
        error_codes   = {}
        error_samples = []
        endpoints_used = {}

        for _idx, (horse_id, row) in enumerate(seen.items(), 1):
            if _idx % 100 == 0:
                _log("INFO", f"Horse history progress: {_idx}/{len(seen)} "
                             f"({fetched} fetched, {errors} errors, "
                             f"codes={error_codes})")
                db.session.commit()
            try:
                # Upsert HorseProfile
                profile = HorseProfile.query.get(horse_id)
                if not profile:
                    profile = HorseProfile(horse_id=horse_id)
                    db.session.add(profile)
                profile.name       = row.horse_name
                profile.colour     = row.colour
                profile.sex        = row.sex
                profile.trainer    = row.trainer
                profile.owner      = row.owner
                profile.updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                db.session.flush()

                # Fetch history from API.
                # Throttled + retried: this job fetches hundreds of horses
                # back to back, which trips the Racing API's rate limit
                # after the first ~50. Previously every non-200 was counted
                # as a generic error and skipped, so a rate limit was
                # indistinguishable from a real miss and most horses never
                # got history.
                resp, _endpoint = _fetch_horse_results(horse_id)
                endpoints_used[_endpoint] = endpoints_used.get(_endpoint, 0) + 1

                if resp is None or resp.status_code != 200:
                    errors += 1
                    code = resp.status_code if resp is not None else 'no_response'
                    error_codes[code] = error_codes.get(code, 0) + 1
                    if len(error_samples) < 5:
                        body = (resp.text[:200] if resp is not None else '')
                        error_samples.append(f"{horse_id}: HTTP {code} {body}")
                    # Throttle on failure too - otherwise a run that's
                    # erroring hammers the API at full speed with no backoff.
                    time.sleep(REQUEST_DELAY_SECONDS)
                    continue

                # Stay under the rate limit for the next horse.
                time.sleep(REQUEST_DELAY_SECONDS)

                results = resp.json().get("results", [])

                for race in results:
                    race_id = race.get("race_id") or ""
                    if not race_id:
                        continue

                    # Find this horse's own run in the field
                    my_run = next(
                        (r for r in race.get("runners", []) if r.get("horse_id") == horse_id),
                        {}
                    )

                    # Upsert HorseRun
                    existing_run = HorseRun.query.filter_by(
                        horse_id=horse_id, race_id=race_id
                    ).first()

                    if existing_run:
                        run = existing_run
                    else:
                        run = HorseRun(horse_id=horse_id, race_id=race_id)
                        db.session.add(run)

                    run.date      = race.get("date") or ""
                    run.course    = race.get("course") or ""
                    run.race_name = race.get("race_name") or ""
                    run.race_type = race.get("type") or ""
                    run.race_class = race.get("class") or ""
                    run.pattern   = race.get("pattern") or ""
                    run.dist      = race.get("dist") or ""
                    run.going     = race.get("going") or ""
                    run.surface   = race.get("surface") or ""
                    run.position  = str(my_run.get("position") or "")
                    run.sp        = my_run.get("sp") or ""
                    run.sp_dec    = str(my_run.get("sp_dec") or "")
                    run.jockey    = my_run.get("jockey") or ""
                    run.trainer   = my_run.get("trainer") or ""
                    run.weight    = my_run.get("weight") or ""
                    run.btn       = str(my_run.get("btn") or "")
                    run.ovr_btn   = str(my_run.get("ovr_btn") or "")
                    run.official_rating = str(my_run.get("or") or "")
                    run.prize     = str(my_run.get("prize") or "")
                    run.comment   = my_run.get("comment") or ""
                    db.session.flush()

                    # Replace field runners
                    HorseRunField.query.filter_by(run_id=run.id).delete()
                    for fr in race.get("runners", []):
                        field_row = HorseRunField(
                            run_id     = run.id,
                            horse_id   = fr.get("horse_id") or "",
                            horse_name = fr.get("horse") or "",
                            position   = str(fr.get("position") or ""),
                            sp         = fr.get("sp") or "",
                            sp_dec     = str(fr.get("sp_dec") or ""),
                            jockey     = fr.get("jockey") or "",
                            trainer    = fr.get("trainer") or "",
                            weight     = fr.get("weight") or "",
                            btn        = str(fr.get("btn") or ""),
                            official_rating = str(fr.get("or") or ""),
                            silk_url   = fr.get("silk_url") or "",
                        )
                        db.session.add(field_row)

                db.session.commit()
                fetched += 1
                time.sleep(REQUEST_DELAY_SECONDS)

            except Exception as e:
                errors += 1
                _log("WARN", f"Horse history error for {horse_id}: {e}")
                db.session.rollback()
                continue

        _log("INFO", f"Horse history endpoints used: {endpoints_used}")
        if error_codes:
            _log("WARN", f"Horse history errors by HTTP status: {error_codes}")
            for s in error_samples:
                _log("WARN", f"Horse history error sample — {s}")
        _log("INFO", f"Horse history sync complete — {fetched} horses fetched, {errors} errors")
        db.session.commit()


def backfill_horse_history(app):
    """Backfill history for every horse ever seen — today's runners AND
    the archived RunnerHistory rows, which is where the bulk of them are.

    Resumable: horses that already have HorseRun rows are skipped, so it
    can be re-run after a restart or a rate-limit stall and will pick up
    where it left off."""
    with app.app_context():
        _log("INFO", "Backfill started")

        # Today's/current runners
        rows = db.session.query(Runner.horse_id, Runner.horse_name,
                                Runner.colour, Runner.sex,
                                Runner.trainer, Runner.owner)\
            .filter(Runner.horse_id != '')\
            .filter(Runner.horse_id != None)\
            .distinct(Runner.horse_id).all()

        by_id = {}
        for r in rows:
            if r.horse_id:
                by_id[r.horse_id] = r

        # Archived runners — the nightly archive has been storing these
        # for every race day, so this is the real historical population.
        hist = db.session.query(RunnerHistory.horse_id, RunnerHistory.horse_name)\
            .filter(RunnerHistory.horse_id != '')\
            .filter(RunnerHistory.horse_id != None)\
            .distinct(RunnerHistory.horse_id).all()

        class _Row:
            __slots__ = ('horse_id', 'horse_name', 'colour', 'sex', 'trainer', 'owner')
            def __init__(self, hid, name):
                self.horse_id = hid
                self.horse_name = name
                self.colour = self.sex = self.trainer = self.owner = ''

        for h in hist:
            if h.horse_id and h.horse_id not in by_id:
                by_id[h.horse_id] = _Row(h.horse_id, h.horse_name)

        # Skip horses that already have history — makes this resumable and
        # avoids re-fetching the ~15k that already succeeded.
        done = {r[0] for r in db.session.query(HorseRun.horse_id).distinct().all()}
        todo = [r for hid, r in by_id.items() if hid not in done]

        _log("INFO", f"Backfill: {len(by_id)} unique horses known, "
                     f"{len(done)} already have history, {len(todo)} to fetch "
                     f"(~{len(todo) * REQUEST_DELAY_SECONDS / 60:.0f} min)")

        fetched = 0
        errors  = 0
        error_codes = {}

        for idx, row in enumerate(todo, 1):
            horse_id = row.horse_id
            if not horse_id:
                continue
            if idx % 100 == 0:
                _log("INFO", f"Backfill progress: {idx}/{len(todo)} "
                             f"({fetched} fetched, {errors} errors, "
                             f"codes={error_codes})")
                db.session.commit()
            try:
                profile = HorseProfile.query.get(horse_id)
                if not profile:
                    profile = HorseProfile(horse_id=horse_id)
                    db.session.add(profile)
                profile.name       = row.horse_name
                profile.colour     = row.colour
                profile.sex        = row.sex
                profile.trainer    = row.trainer
                profile.owner      = row.owner
                profile.updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                db.session.flush()

                resp, _endpoint = _fetch_horse_results(horse_id)

                if resp is None or resp.status_code != 200:
                    errors += 1
                    code = resp.status_code if resp is not None else 'no_response'
                    error_codes[code] = error_codes.get(code, 0) + 1
                    time.sleep(REQUEST_DELAY_SECONDS)
                    continue

                results = resp.json().get("results", [])
                for race in results:
                    race_id = race.get("race_id") or ""
                    if not race_id:
                        continue

                    my_run = next(
                        (r for r in race.get("runners", []) if r.get("horse_id") == horse_id),
                        {}
                    )

                    existing_run = HorseRun.query.filter_by(
                        horse_id=horse_id, race_id=race_id
                    ).first()

                    if existing_run:
                        run = existing_run
                    else:
                        run = HorseRun(horse_id=horse_id, race_id=race_id)
                        db.session.add(run)

                    run.date      = race.get("date") or ""
                    run.course    = race.get("course") or ""
                    run.race_name = race.get("race_name") or ""
                    run.race_type = race.get("type") or ""
                    run.race_class = race.get("class") or ""
                    run.pattern   = race.get("pattern") or ""
                    run.dist      = race.get("dist") or ""
                    run.going     = race.get("going") or ""
                    run.surface   = race.get("surface") or ""
                    run.position  = str(my_run.get("position") or "")
                    run.sp        = my_run.get("sp") or ""
                    run.sp_dec    = str(my_run.get("sp_dec") or "")
                    run.jockey    = my_run.get("jockey") or ""
                    run.trainer   = my_run.get("trainer") or ""
                    run.weight    = my_run.get("weight") or ""
                    run.btn       = str(my_run.get("btn") or "")
                    run.ovr_btn   = str(my_run.get("ovr_btn") or "")
                    run.official_rating = str(my_run.get("or") or "")
                    run.prize     = str(my_run.get("prize") or "")
                    run.comment   = my_run.get("comment") or ""
                    db.session.flush()

                    HorseRunField.query.filter_by(run_id=run.id).delete()
                    for fr in race.get("runners", []):
                        field_row = HorseRunField(
                            run_id     = run.id,
                            horse_id   = fr.get("horse_id") or "",
                            horse_name = fr.get("horse") or "",
                            position   = str(fr.get("position") or ""),
                            sp         = fr.get("sp") or "",
                            sp_dec     = str(fr.get("sp_dec") or ""),
                            jockey     = fr.get("jockey") or "",
                            trainer    = fr.get("trainer") or "",
                            weight     = fr.get("weight") or "",
                            btn        = str(fr.get("btn") or ""),
                            official_rating = str(fr.get("or") or ""),
                            silk_url   = fr.get("silk_url") or "",
                        )
                        db.session.add(field_row)

                db.session.commit()
                fetched += 1
                time.sleep(REQUEST_DELAY_SECONDS)

            except Exception as e:
                errors += 1
                _log("WARN", f"Backfill error for {horse_id}: {e}")
                db.session.rollback()
                continue

        if error_codes:
            _log("WARN", f"Backfill errors by HTTP status: {error_codes}")
        _log("INFO", f"Backfill complete — {fetched} horses, {errors} errors")
        db.session.commit()
