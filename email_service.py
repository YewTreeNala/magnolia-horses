import os
import requests
from datetime import datetime


SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
FROM_EMAIL       = os.getenv('FROM_EMAIL', 'alerts@magnoliahorses.com')
SITE_URL         = os.getenv('SITE_URL', 'https://magnoliahorses.com')


def send_email(to_email, to_name, subject, html_body):
    if not SENDGRID_API_KEY:
        print(f'[Email] No API key — would have sent to {to_email}: {subject}')
        return False
    payload = {
        'personalizations': [{'to': [{'email': to_email, 'name': to_name}]}],
        'from':    {'email': FROM_EMAIL, 'name': 'Magnolia Horses'},
        'subject': subject,
        'content': [{'type': 'text/html', 'value': html_body}]
    }
    response = requests.post(
        'https://api.sendgrid.com/v3/mail/send',
        json=payload,
        headers={'Authorization': f'Bearer {SENDGRID_API_KEY}', 'Content-Type': 'application/json'}
    )
    if response.status_code == 202:
        print(f'[Email] Sent to {to_email}: {subject}')
        return True
    print(f'[Email] Failed ({response.status_code}): {response.text}')
    return False


def _badge(reason):
    if 'Favourite' in reason and 'Search' in reason:
        bg, col = '#6b2d8b1a', '#6b2d8b'
    elif reason == 'Favourite':
        bg, col = '#8b3a3a1a', '#8b3a3a'
    else:
        bg, col = '#185fa51a', '#185fa5'
    return (
        f'<span style="font-size:11px;padding:2px 8px;border-radius:20px;'
        f'background:{bg};color:{col};border:0.5px solid {col}44;white-space:nowrap">'
        f'{reason}</span>'
    )


def build_combined_email(user_name, runners):
    today_str = datetime.now().strftime('%A %d %B %Y')
    n         = len(runners)
    n_label   = f'{n} runner{"s" if n != 1 else ""}'

    rows = ''
    for r in runners:
        rows += (
            '<tr style="border-bottom:1px solid #f0e8e4;">'
            f'<td style="padding:8px 10px;color:#8b3a3a;font-weight:600;white-space:nowrap">{r["time"]}</td>'
            f'<td style="padding:8px 10px;font-weight:600;color:#2a1f14">{r["horse_name"]}</td>'
            f'<td style="padding:8px 10px;color:#6b5a48;font-size:12px">{r["meeting"]}</td>'
            f'<td style="padding:8px 10px;color:#6b5a48;font-size:12px">{r["jockey"] or "."}</td>'
            f'<td style="padding:8px 10px;color:#6b5a48;font-size:12px">{r["trainer"] or "."}</td>'
            f'<td style="padding:8px 10px;color:#6b5a48;font-size:12px">{r["colour"] or "."}</td>'
            f'<td style="padding:8px 10px">{_badge(r["reason"])}</td>'
            '</tr>'
        )

    table = (
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#f5ece8;">'
        + ''.join(
            f'<th style="padding:6px 10px;text-align:left;color:#8b3a3a;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">{h}</th>'
            for h in ['Time', 'Horse', 'Meeting', 'Jockey', 'Trainer', 'Colour', 'Reason']
        )
        + f'</tr></thead><tbody>{rows}</tbody></table>'
    )

    legend = (
        '<div style="margin-top:14px;font-size:12px;color:#9c8a78;">'
        '<span style="margin-right:16px">'
        '<span style="background:#8b3a3a1a;color:#8b3a3a;border:0.5px solid #8b3a3a44;padding:1px 7px;border-radius:20px;font-size:11px">Favourite</span>'
        ' Horse you tagged</span>'
        '<span>'
        '<span style="background:#185fa51a;color:#185fa5;border:0.5px solid #185fa544;padding:1px 7px;border-radius:20px;font-size:11px">Search: name</span>'
        ' Matches a saved search</span>'
        '</div>'
    )

    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"/></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;background:#f5f1eb;margin:0;padding:20px;">'
        '<div style="max-width:700px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid rgba(0,0,0,0.1);">'
        '<div style="background:#8b3a3a;padding:24px 28px;">'
        '<div style="font-family:Georgia,serif;font-size:22px;color:#fff;font-weight:600;">Magnolia Horses</div>'
        f'<div style="color:rgba(255,255,255,0.75);font-size:13px;margin-top:4px;">Morning alert &mdash; {today_str}</div>'
        '</div>'
        '<div style="padding:24px 28px;">'
        f'<p style="color:#2a1f14;font-size:15px;margin:0 0 6px;">Good morning {user_name},</p>'
        f'<p style="color:#6b5a48;font-size:14px;margin:0 0 20px;">Here {"are" if n != 1 else "is"} your {n_label} to follow today, sorted by race time.</p>'
        + table + legend +
        f'<div style="margin-top:24px;"><a href="{SITE_URL}" style="background:#8b3a3a;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:500;">View race cards</a></div>'
        '</div>'
        f'<div style="padding:16px 28px;border-top:1px solid #f0e8e4;font-size:12px;color:#9c8a78;">'
        f'Manage your <a href="{SITE_URL}/my-horses" style="color:#8b3a3a;">favourites and saved searches</a>'
        '</div></div></body></html>'
    )


def send_morning_alerts(app):
    from models import db, User, Runner, Race, Meeting
    from datetime import date
    import json
    from rapidfuzz import fuzz
    import jellyfish

    with app.app_context():
        today       = date.today().strftime('%Y-%m-%d')
        all_runners = db.session.query(Runner).join(Race).join(Meeting)\
            .filter(Meeting.date == today).all()

        alerts_sent = 0

        for user in User.query.all():
            runner_reasons = {}   # runner.id -> {'runner': r, 'reasons': [...]}

            # 1. Favourite horses
            tagged_names = {t.horse_name.lower() for t in user.tagged}
            for r in all_runners:
                if r.horse_name.lower() in tagged_names:
                    runner_reasons.setdefault(r.id, {'runner': r, 'reasons': []})
                    runner_reasons[r.id]['reasons'].append('Favourite')

            # 2. Saved search alerts
            for saved in [s for s in user.searches if s.alert]:
                try:
                    f = json.loads(saved.filters)
                except Exception:
                    continue
                for r in all_runners:
                    if f.get('colour')  and f['colour'].lower()  not in (r.colour or '').lower():          continue
                    if f.get('meeting') and f['meeting'].lower() not in r.race.meeting.name.lower():        continue
                    if f.get('jockey')  and f['jockey'].lower()  != (r.jockey or '').lower():              continue
                    if f.get('trainer') and f['trainer'].lower() != (r.trainer or '').lower():              continue
                    if f.get('owner')   and f['owner'].lower()   != (r.owner or '').lower():               continue
                    hf = f.get('horse', '').strip()
                    if hf:
                        nl = r.horse_name.lower(); sl = hf.lower()
                        if f.get('fuzzy', True):
                            ok = (sl in nl or fuzz.partial_ratio(sl, nl) >= 75
                                  or any(jellyfish.soundex(w) == jellyfish.soundex(s)
                                         for w in nl.split() for s in sl.split() if len(w) > 2 and len(s) > 2))
                        else:
                            ok = sl in nl
                        if not ok:
                            continue
                    runner_reasons.setdefault(r.id, {'runner': r, 'reasons': []})
                    runner_reasons[r.id]['reasons'].append(f'Search: {saved.name}')

            if not runner_reasons:
                continue

            combined = sorted([
                {
                    'horse_name': e['runner'].horse_name,
                    'meeting':    e['runner'].race.meeting.name,
                    'time':       e['runner'].race.time,
                    'jockey':     e['runner'].jockey,
                    'trainer':    e['runner'].trainer,
                    'colour':     e['runner'].colour,
                    'reason':     ' & '.join(e['reasons']),
                }
                for e in runner_reasons.values()
            ], key=lambda x: x['time'])

            n       = len(combined)
            subject = f"Magnolia Horses: {n} runner{'s' if n != 1 else ''} to follow today"
            send_email(user.email, user.name, subject, build_combined_email(user.name, combined))
            alerts_sent += 1

        print(f'[Alerts] Morning alerts sent to {alerts_sent} users')
