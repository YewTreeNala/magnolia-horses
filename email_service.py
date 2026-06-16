import os
import requests
from datetime import datetime

RESEND_API_KEY = os.getenv('RESEND_API_KEY')
_last_email_error = ''
FROM_EMAIL     = os.getenv('FROM_EMAIL', 'alerts@magnoliahorses.com')
SITE_URL       = os.getenv('SITE_URL', 'https://magnoliahorses.com')

UK_COURSES = {
    'ascot', 'ayr', 'bath', 'beverley', 'brighton', 'carlisle', 'catterick',
    'chelmsford', 'cheltenham', 'chepstow', 'chester', 'doncaster', 'epsom',
    'exeter', 'ffos las', 'goodwood', 'hamilton', 'haydock', 'hereford',
    'huntingdon', 'kempton', 'leicester', 'lingfield', 'ludlow', 'market rasen',
    'musselburgh', 'newbury', 'newcastle', 'newmarket', 'nottingham', 'perth',
    'plumpton', 'pontefract', 'redcar', 'ripon', 'salisbury', 'sandown',
    'sedgefield', 'southwell', 'stratford', 'taunton', 'thirsk', 'uttoxeter',
    'warwick', 'wetherby', 'windsor', 'wolverhampton', 'worcester', 'wincanton',
    'yarmouth', 'york'
}


def is_uk_course(name):
    return (name or '').strip().lower() in UK_COURSES


def send_email(to_email, to_name, subject, html_body, user_id=None):
    from datetime import datetime as _dt
    status = 'sent'

    if not RESEND_API_KEY:
        print(f'[Email] No API key - would have sent to {to_email}: {subject}')
        status = 'no_api_key'
    else:
        payload = {
            'from':    f'Magnolia Horses <{FROM_EMAIL}>',
            'to':      [to_email],
            'subject': subject,
            'html':    html_body,
        }
        response = requests.post(
            'https://api.resend.com/emails',
            json=payload,
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            }
        )
        if response.status_code == 200 or response.status_code == 201:
            print(f'[Email] Sent to {to_email}: {subject}')
        else:
            error_detail = f'Email failed ({response.status_code}): {response.text[:300]}'
            print(f'[Email] {error_detail}')
            status = 'failed'
            global _last_email_error
            _last_email_error = error_detail

    if user_id is not None:
        try:
            from models import db, EmailLog
            log = EmailLog(
                user_id=user_id,
                subject=subject,
                html_body=html_body,
                status=status,
                sent_at=_dt.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            db.session.add(log)
            db.session.commit()
        except Exception as e:
            print(f'[Email] Failed to log email: {e}')

    return status == 'sent'


def _badge(reason):
    if 'Favourite' in reason and 'Search' in reason:
        bg, col = '#6b2d8b1a', '#6b2d8b'
    elif reason == 'Favourite':
        bg, col = '#8b3a3a1a', '#8b3a3a'
    else:
        bg, col = '#185fa51a', '#185fa5'
    return (
        '<span style="font-size:11px;padding:2px 8px;border-radius:20px;'
        'background:' + bg + ';color:' + col + ';border:0.5px solid ' + col + '44;white-space:nowrap">'
        + reason + '</span>'
    )


def build_combined_email(user_name, runners):
    today_str = datetime.now().strftime('%A %d %B %Y')
    n         = len(runners)
    n_label   = str(n) + ' runner' + ('s' if n != 1 else '')

    meetings_order = []
    meetings_map   = {}
    for r in runners:
        m = r['meeting']
        if m not in meetings_map:
            meetings_map[m] = []
            meetings_order.append(m)
        meetings_map[m].append(r)

    th_style = (
        'padding:5px 10px;text-align:left;color:#8b3a3a;'
        'font-size:10px;text-transform:uppercase;letter-spacing:0.05em'
    )
    meeting_blocks = ''
    for meeting_name in meetings_order:
        rows = ''
        for r in meetings_map[meeting_name]:
            rows += (
                '<tr style="border-bottom:1px solid #f0e8e4;">'
                '<td style="padding:7px 10px;color:#8b3a3a;font-weight:600;white-space:nowrap;width:56px">'
                + (r['time'] or '') +
                '</td>'
                '<td style="padding:7px 10px;font-weight:600;color:#2a1f14">'
                + (r['horse_name'] or '') +
                '</td>'
                '<td style="padding:7px 10px;color:#6b5a48;font-size:12px">'
                + (r['jockey'] or '-') +
                '</td>'
                '<td style="padding:7px 10px;color:#6b5a48;font-size:12px">'
                + (r['trainer'] or '-') +
                '</td>'
                '<td style="padding:7px 10px;color:#6b5a48;font-size:12px">'
                + (r['colour'] or '-') +
                '</td>'
                '<td style="padding:7px 10px">' + _badge(r['reason']) + '</td>'
                '</tr>'
            )
        meeting_blocks += (
            '<div style="margin-bottom:18px;">'
            '<div style="background:#8b3a3a;color:#fff;font-size:13px;font-weight:600;'
            'padding:7px 12px;border-radius:6px 6px 0 0;letter-spacing:0.02em">'
            + meeting_name +
            '</div>'
            '<table style="width:100%;border-collapse:collapse;font-size:13px;'
            'border:0.5px solid #e8ddd8;border-top:none;">'
            '<thead><tr style="background:#f5ece8;">'
            '<th style="' + th_style + '">Time</th>'
            '<th style="' + th_style + '">Horse</th>'
            '<th style="' + th_style + '">Jockey</th>'
            '<th style="' + th_style + '">Trainer</th>'
            '<th style="' + th_style + '">Colour</th>'
            '<th style="' + th_style + '">Reason</th>'
            '</tr></thead>'
            '<tbody>' + rows + '</tbody>'
            '</table></div>'
        )

    legend = (
        '<div style="margin-top:8px;font-size:12px;color:#9c8a78;">'
        '<span style="margin-right:14px">'
        '<span style="background:#8b3a3a1a;color:#8b3a3a;border:0.5px solid #8b3a3a44;'
        'padding:1px 7px;border-radius:20px;font-size:11px">Favourite</span>'
        ' Horse you tagged</span>'
        '<span>'
        '<span style="background:#185fa51a;color:#185fa5;border:0.5px solid #185fa544;'
        'padding:1px 7px;border-radius:20px;font-size:11px">Search: name</span>'
        ' Saved search match</span>'
        '</div>'
    )

    here_are = 'are' if n != 1 else 'is'

    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"/></head>'
        '<body style="font-family:Arial,sans-serif;background:#f5f1eb;margin:0;padding:20px;">'
        '<div style="max-width:680px;margin:0 auto;background:#fff;border-radius:12px;'
        'overflow:hidden;border:1px solid rgba(0,0,0,0.1);">'
        '<div style="background:#8b3a3a;padding:24px 28px;">'
        '<div style="font-family:Georgia,serif;font-size:22px;color:#fff;font-weight:600;">'
        'Magnolia Horses</div>'
        '<div style="color:rgba(255,255,255,0.75);font-size:13px;margin-top:4px;">'
        'Morning alert &mdash; ' + today_str + '</div>'
        '</div>'
        '<div style="padding:24px 28px;">'
        '<p style="color:#2a1f14;font-size:15px;margin:0 0 6px;">Good morning ' + user_name + ',</p>'
        '<p style="color:#6b5a48;font-size:14px;margin:0 0 20px;">'
        'Here ' + here_are + ' your ' + n_label + ' to follow today, grouped by meeting.</p>'
        + meeting_blocks + legend +
        '<div style="margin-top:24px;">'
        '<a href="' + SITE_URL + '" style="background:#8b3a3a;color:#fff;padding:10px 20px;'
        'border-radius:8px;text-decoration:none;font-size:14px;font-weight:500;">'
        'View race cards</a></div>'
        '</div>'
        '<div style="padding:16px 28px;border-top:1px solid #f0e8e4;font-size:12px;color:#9c8a78;">'
        'Manage your <a href="' + SITE_URL + '/my-horses" style="color:#8b3a3a;">'
        'favourites and saved searches</a>'
        '</div></div></body></html>'
    )


def _matches_filters(r, f):
    """Check if a runner matches a saved search filter dict."""
    from rapidfuzz import fuzz
    import jellyfish

    # uk_only — use consistent helper
    if f.get('uk_only') and not is_uk_course(r.race.meeting.name):
        return False
    if f.get('colour') and f['colour'].lower() not in (r.colour or '').lower():
        return False
    if f.get('meeting') and f['meeting'].lower() not in r.race.meeting.name.lower():
        return False
    if f.get('jockey') and f['jockey'].lower() != (r.jockey or '').lower():
        return False
    if f.get('trainer') and f['trainer'].lower() != (r.trainer or '').lower():
        return False
    if f.get('owner') and f['owner'].lower() != (r.owner or '').lower():
        return False
    hf = (f.get('horse') or '').strip()
    if hf:
        use_fuzzy = f.get('fuzzy', True)
        nl = r.horse_name.lower()
        sl = hf.lower()
        if use_fuzzy:
            match = (
                sl in nl
                or fuzz.partial_ratio(sl, nl) >= 75
                or any(
                    jellyfish.soundex(w) == jellyfish.soundex(s)
                    for w in nl.split()
                    for s in sl.split()
                    if len(w) > 2 and len(s) > 2
                )
            )
        else:
            match = sl in nl
        if not match:
            return False
    return True


def _build_combined_for_user(user, all_runners):
    import json
    runner_reasons = {}

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
            if _matches_filters(r, f):
                runner_reasons.setdefault(r.id, {'runner': r, 'reasons': []})
                runner_reasons[r.id]['reasons'].append('Search: ' + saved.name)

    if not runner_reasons:
        return []

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
    ], key=lambda x: (x['meeting'], x['time']))

    return combined


def send_morning_alerts(app):
    from models import db, User, Runner, Race, Meeting
    from datetime import date

    with app.app_context():
        today       = date.today().strftime('%Y-%m-%d')
        all_runners = db.session.query(Runner).join(Race).join(Meeting)\
            .filter(Meeting.date == today).all()

        alerts_sent = 0
        for user in User.query.all():
            combined = _build_combined_for_user(user, all_runners)
            if not combined:
                continue
            n       = len(combined)
            subject = ('Magnolia Horses: ' + str(n) + ' runner' +
                       ('s' if n != 1 else '') + ' to follow today')
            send_email(
                user.email, user.name, subject,
                build_combined_email(user.name, combined),
                user_id=user.id
            )
            alerts_sent += 1

        print(f'[Alerts] Morning alerts sent to {alerts_sent} users')


def send_morning_alerts_for_user(user_id, app):
    from models import db, User, Runner, Race, Meeting
    from datetime import date

    with app.app_context():
        user = User.query.get(user_id)
        if not user:
            return {'status': 'error', 'message': 'User not found'}

        today       = date.today().strftime('%Y-%m-%d')
        all_runners = db.session.query(Runner).join(Race).join(Meeting)\
            .filter(Meeting.date == today).all()

        combined = _build_combined_for_user(user, all_runners)
        if not combined:
            return {
                'status':  'no_runners',
                'message': 'No runners matched your favourites or saved searches today'
            }

        n       = len(combined)
        subject = ('[Test] Magnolia Horses: ' + str(n) + ' runner' +
                   ('s' if n != 1 else '') + ' to follow today')
        ok = send_email(
            user.email, user.name, subject,
            build_combined_email(user.name, combined),
            user_id=user_id
        )
        if ok:
            return {'status': 'sent', 'message': 'Test email sent to ' + user.email + ' with ' + str(n) + ' runners'}
        return {'status': 'failed', 'message': _last_email_error or 'Email send failed'}
