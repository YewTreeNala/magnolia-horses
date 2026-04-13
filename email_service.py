import os
import requests
from datetime import datetime


SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
FROM_EMAIL       = os.getenv('FROM_EMAIL', 'alerts@magnoliahorses.com')
SITE_URL         = os.getenv('SITE_URL', 'https://magnoliahorses.com')


def send_email(to_email, to_name, subject, html_body):
    """Send a single email via SendGrid."""
    if not SENDGRID_API_KEY:
        print(f'[Email] No API key — would have sent to {to_email}: {subject}')
        return False

    payload = {
        'personalizations': [{
            'to': [{'email': to_email, 'name': to_name}]
        }],
        'from': {'email': FROM_EMAIL, 'name': 'Magnolia Horses'},
        'subject': subject,
        'content': [{'type': 'text/html', 'value': html_body}]
    }

    response = requests.post(
        'https://api.sendgrid.com/v3/mail/send',
        json=payload,
        headers={
            'Authorization': f'Bearer {SENDGRID_API_KEY}',
            'Content-Type': 'application/json'
        }
    )

    if response.status_code == 202:
        print(f'[Email] Sent to {to_email}: {subject}')
        return True
    else:
        print(f'[Email] Failed ({response.status_code}): {response.text}')
        return False


def build_alert_email(user_name, runners_today):
    """Build the HTML for the morning alert email."""
    today_str = datetime.now().strftime('%A %d %B %Y')

    rows = ''
    for r in runners_today:
        rows += f'''
        <tr style="border-bottom:1px solid #f0e8e4;">
          <td style="padding:10px 12px;font-weight:600;color:#2a1f14">{r['horse_name']}</td>
          <td style="padding:10px 12px;color:#6b5a48">{r['meeting']}</td>
          <td style="padding:10px 12px;color:#6b5a48">{r['time']}</td>
          <td style="padding:10px 12px;color:#6b5a48">{r['race_name']}</td>
          <td style="padding:10px 12px;color:#6b5a48">{r['jockey'] or '—'}</td>
        </tr>'''

    return f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f1eb;margin:0;padding:20px;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid rgba(0,0,0,0.1);">

    <div style="background:#8b3a3a;padding:24px 28px;">
      <div style="font-family:Georgia,serif;font-size:22px;color:#fff;font-weight:600;">Magnolia Horses</div>
      <div style="color:rgba(255,255,255,0.75);font-size:13px;margin-top:4px;">Morning runner alert</div>
    </div>

    <div style="padding:24px 28px;">
      <p style="color:#2a1f14;font-size:15px;margin:0 0 6px;">Good morning {user_name},</p>
      <p style="color:#6b5a48;font-size:14px;margin:0 0 20px;">
        {len(runners_today)} of your tagged horse{'s are' if len(runners_today) != 1 else ' is'} running today, {today_str}.
      </p>

      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#f5ece8;">
            <th style="padding:8px 12px;text-align:left;color:#8b3a3a;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Horse</th>
            <th style="padding:8px 12px;text-align:left;color:#8b3a3a;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Meeting</th>
            <th style="padding:8px 12px;text-align:left;color:#8b3a3a;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Time</th>
            <th style="padding:8px 12px;text-align:left;color:#8b3a3a;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Race</th>
            <th style="padding:8px 12px;text-align:left;color:#8b3a3a;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Jockey</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <div style="margin-top:24px;">
        <a href="{SITE_URL}" style="background:#8b3a3a;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:500;">
          View race cards
        </a>
      </div>
    </div>

    <div style="padding:16px 28px;border-top:1px solid #f0e8e4;font-size:12px;color:#9c8a78;">
      You're receiving this because you tagged these horses on Magnolia Horses.
      <a href="{SITE_URL}/account" style="color:#8b3a3a;">Manage your alerts</a>
    </div>
  </div>
</body>
</html>'''


def send_morning_alerts(app):
    """Called by the scheduler each morning — finds tagged horses running today."""
    from models import db, User, TaggedHorse, Runner, Race, Meeting
    from datetime import date

    with app.app_context():
        today = date.today().strftime('%Y-%m-%d')

        # Get all users with tagged horses
        users = User.query.all()
        alerts_sent = 0

        for user in users:
            if not user.tagged:
                continue

            tagged_names = [t.horse_name.lower() for t in user.tagged]

            # Find which tagged horses are running today
            runners_today = []
            runners = db.session.query(Runner).join(Race).join(Meeting)\
                .filter(Meeting.date == today).all()

            for r in runners:
                if r.horse_name.lower() in tagged_names:
                    runners_today.append({
                        'horse_name': r.horse_name,
                        'meeting':    r.race.meeting.name,
                        'time':       r.race.time,
                        'race_name':  r.race.name,
                        'jockey':     r.jockey,
                    })

            if runners_today:
                html = build_alert_email(user.name, runners_today)
                subject = f"{len(runners_today)} of your horses {'are' if len(runners_today) != 1 else 'is'} running today"
                send_email(user.email, user.name, subject, html)
                alerts_sent += 1

        print(f'[Alerts] Morning alerts sent to {alerts_sent} users')
