"""
Read-only Betfair Exchange market lookup — used to build a direct link
to a race's WIN market so a runner's horse-detail popup can offer a
"Bet on Betfair" button.

This does NOT place bets — only listMarketCatalogue (read-only), so it
works fine with a free Delayed App Key. Reuses the same cert-based
login approach as the VPS auto-betting system (Betfair requires this
for unattended/programmatic login — plain username/password triggers
2FA a script can't answer).

Credentials: by default, uses the shared account configured via
Railway env vars (BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD,
BETFAIR_CERT_PEM, BETFAIR_KEY_PEM — the cert/key as raw PEM text,
written to temp files at first use since Railway has no persistent
file storage). If a user has personal encrypted credentials set (see
models.py / betfair_crypto.py), those are used instead — no UI exists
yet to set these, so in practice every lookup currently uses the
shared default.
"""

import os
import re
import time
import tempfile
import logging
import difflib
import requests
from datetime import datetime, timedelta

from betfair_crypto import decrypt

log = logging.getLogger('betfair_lookup')

IDENTITY_URL = 'https://identitysso-cert.betfair.com/api/certlogin'
BETTING_URL = 'https://api.betfair.com/exchange/betting/json-rpc/v1'
HORSE_RACING_EVENT_TYPE_ID = '7'
EXCHANGE_MARKET_URL = 'https://www.betfair.com/exchange/plus/horse-racing/market/{market_id}'

SESSION_MAX_AGE = 3600 * 3
_sessions = {}  # keyed by (app_key, username) -> {'token':..., 'time':...}
_cert_temp_files = {}  # keyed by content hash -> file path, so we don't
                        # rewrite the same PEM content to disk repeatedly


class BetfairLookupError(Exception):
    pass


def _write_temp_pem(content, suffix):
    """Write PEM content to a temp file once, reusing the path on
    subsequent calls with the same content."""
    key = (hash(content), suffix)
    if key in _cert_temp_files and os.path.exists(_cert_temp_files[key]):
        return _cert_temp_files[key]
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, 'w') as f:
        f.write(content)
    _cert_temp_files[key] = path
    return path


def _default_credentials():
    app_key  = os.environ.get('BETFAIR_APP_KEY', '').strip()
    username = os.environ.get('BETFAIR_USERNAME', '').strip()
    password = os.environ.get('BETFAIR_PASSWORD', '').strip()
    cert_pem = os.environ.get('BETFAIR_CERT_PEM', '').strip()
    key_pem  = os.environ.get('BETFAIR_KEY_PEM', '').strip()
    if not all([app_key, username, password, cert_pem, key_pem]):
        return None
    cert_file = _write_temp_pem(cert_pem, '.crt')
    key_file = _write_temp_pem(key_pem, '.key')
    return {'app_key': app_key, 'username': username, 'password': password,
            'cert_file': cert_file, 'key_file': key_file}


def _user_credentials(user):
    """Decrypt a user's personal Betfair credentials, if set. Returns
    None if any required field is missing or fails to decrypt — falls
    back to the shared default in that case."""
    if user is None:
        return None
    app_key  = decrypt(getattr(user, 'betfair_app_key_enc', None))
    username = decrypt(getattr(user, 'betfair_username_enc', None))
    password = decrypt(getattr(user, 'betfair_password_enc', None))
    cert_pem = decrypt(getattr(user, 'betfair_cert_enc', None))
    key_pem  = decrypt(getattr(user, 'betfair_key_enc', None))
    if not all([app_key, username, password, cert_pem, key_pem]):
        return None
    cert_file = _write_temp_pem(cert_pem, '.crt')
    key_file = _write_temp_pem(key_pem, '.key')
    return {'app_key': app_key, 'username': username, 'password': password,
            'cert_file': cert_file, 'key_file': key_file}


def _resolve_credentials(user):
    return _user_credentials(user) or _default_credentials()


def _login(creds):
    resp = requests.post(
        IDENTITY_URL,
        cert=(creds['cert_file'], creds['key_file']),
        headers={'X-Application': creds['app_key'],
                  'Content-Type': 'application/x-www-form-urlencoded'},
        data={'username': creds['username'], 'password': creds['password']},
        timeout=15,
    )
    if resp.status_code != 200:
        raise BetfairLookupError(f"Login HTTP {resp.status_code}")
    data = resp.json()
    if data.get('loginStatus') != 'SUCCESS':
        raise BetfairLookupError(f"Login failed: {data.get('loginStatus')}")
    return data['sessionToken']


def _get_session(creds):
    session_key = (creds['app_key'], creds['username'])
    cached = _sessions.get(session_key)
    if cached and (time.time() - cached['time']) < SESSION_MAX_AGE:
        return cached['token']
    token = _login(creds)
    _sessions[session_key] = {'token': token, 'time': time.time()}
    return token


def _api_call(creds, method, params, retry=True):
    token = _get_session(creds)
    payload = [{'jsonrpc': '2.0', 'method': f'SportsAPING/v1.0/{method}',
                'params': params, 'id': 1}]
    headers = {'X-Application': creds['app_key'], 'X-Authentication': token,
               'Content-Type': 'application/json'}
    resp = requests.post(BETTING_URL, json=payload, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise BetfairLookupError(f"API HTTP {resp.status_code}")
    result = resp.json()[0]
    if 'error' in result:
        err = result['error']
        code = (err.get('data') or {}).get('APINGException', {}).get('errorCode', '')
        if code == 'INVALID_SESSION_INFORMATION' and retry:
            session_key = (creds['app_key'], creds['username'])
            _sessions.pop(session_key, None)
            return _api_call(creds, method, params, retry=False)
        raise BetfairLookupError(f"API error: {err}")
    return result.get('result')


def _strip_country(name):
    return re.sub(r'\s*\([^)]{2,4}\)\s*$', '', name or '').strip()


def _normalise(name):
    return re.sub(r"[^a-z0-9]", '', _strip_country(name).lower())


def _match_runner(target_horse, runners):
    target_n = _normalise(target_horse)
    best = (None, 0.0)
    for r in runners:
        r_n = _normalise(r['runnerName'])
        if target_n == r_n:
            return r['selectionId'], 1.0
        score = difflib.SequenceMatcher(None, target_n, r_n).ratio()
        if score > best[1]:
            best = (r['selectionId'], score)
    if best[1] < 0.82:
        return None, best[1]
    return best


def get_betfair_market_url(course, time_str, horse_name, race_date, user=None):
    """
    Look up the Betfair Exchange WIN market for a given course/time/date
    and return a direct link to it, or None if nothing could be found
    or credentials aren't configured. Never raises for expected failure
    cases (no credentials, no market, no runner match) — logs and
    returns None so the popup can just omit the button gracefully.
    """
    creds = _resolve_credentials(user)
    if creds is None:
        log.info("[BetfairLookup] No usable credentials (shared default not "
                 "configured and no personal creds set) — skipping")
        return None

    try:
        hh, mm = time_str.split(':')
        off_dt_naive = datetime(race_date.year, race_date.month, race_date.day,
                                 int(hh), int(mm))
    except (ValueError, AttributeError):
        return None

    try:
        import pytz
        uk_tz = pytz.timezone('Europe/London')
        off_dt_uk = uk_tz.localize(off_dt_naive)
        off_dt_utc = off_dt_uk.astimezone(pytz.utc).replace(tzinfo=None)
    except Exception as e:
        log.error(f"[BetfairLookup] Timezone conversion failed: {e}")
        return None

    window_start = (off_dt_utc - timedelta(minutes=20)).strftime('%Y-%m-%dT%H:%M:%SZ')
    window_end = (off_dt_utc + timedelta(minutes=20)).strftime('%Y-%m-%dT%H:%M:%SZ')

    params = {
        'filter': {
            'eventTypeIds': [HORSE_RACING_EVENT_TYPE_ID],
            'marketCountries': ['GB', 'IE'],
            'marketTypeCodes': ['WIN'],
            'textQuery': course,
            'marketStartTime': {'from': window_start, 'to': window_end},
        },
        'maxResults': 10,
        'marketProjection': ['RUNNER_DESCRIPTION', 'EVENT', 'MARKET_START_TIME'],
    }

    try:
        catalogue = _api_call(creds, 'listMarketCatalogue', params)
    except BetfairLookupError as e:
        log.error(f"[BetfairLookup] Market lookup failed for {course} {time_str}: {e}")
        return None

    if not catalogue:
        return None

    course_n = _normalise(course)
    candidates = []
    for m in catalogue:
        venue = (m.get('event') or {}).get('venue') or (m.get('event') or {}).get('name') or ''
        venue_score = difflib.SequenceMatcher(None, course_n, _normalise(venue)).ratio()
        if venue_score < 0.6:
            continue
        start_str = m.get('marketStartTime')
        start_dt = None
        for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ'):
            try:
                start_dt = datetime.strptime(start_str, fmt)
                break
            except (ValueError, TypeError):
                continue
        time_diff = abs((start_dt - off_dt_utc).total_seconds()) if start_dt else float('inf')
        candidates.append((time_diff, venue_score, m))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], -c[1]))
    best = candidates[0][2]

    selection_id, score = _match_runner(horse_name, [
        {'selectionId': r['selectionId'], 'runnerName': r['runnerName']}
        for r in best.get('runners', [])
    ])
    # Even without a confident runner match, the market itself (course +
    # time) is still useful — link to the market page either way; the
    # runner match is only used to decide whether we're confident enough
    # to have found the RIGHT race at all.
    if selection_id is None and score < 0.5:
        log.info(f"[BetfairLookup] Found a market for {course} {time_str} but "
                 f"runner match too weak ({score:.2f}) — likely wrong race")
        return None

    return EXCHANGE_MARKET_URL.format(market_id=best['marketId'])
