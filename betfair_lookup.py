"""
Betfair market lookup for Magnolia Horses.

Betfair's API only accepts connections from UK/Ireland IPs, and this
app runs on Railway (US East), so it cannot call Betfair directly —
attempts fail with BETTING_RESTRICTED_LOCATION.

Instead, this module calls a small proxy service (betfair_proxy.py)
running on the UK VPS in Portsmouth, which already has working Betfair
credentials and certs. The proxy does the lookup and returns the
market URL and current best back price.

Required Railway env vars:
    BETFAIR_PROXY_URL     e.g. http://<vps-host-or-ip>:5001
    BETFAIR_PROXY_SECRET  must match [betfair_proxy] shared_secret
                          in the VPS's config.ini

If either is unset, lookups return None and the UI simply omits the
button — no errors surfaced to the user.
"""

import os
import logging
import requests

log = logging.getLogger('betfair_lookup')

TIMEOUT_SECONDS = 8


def _proxy_config():
    url = (os.environ.get('BETFAIR_PROXY_URL') or '').strip().rstrip('/')
    secret = (os.environ.get('BETFAIR_PROXY_SECRET') or '').strip()
    if not url or not secret:
        return None, None
    return url, secret


def get_betfair_market_info(course, time_str, horse_name, race_date, user=None):
    """
    Look up the Betfair Exchange WIN market for a race via the UK proxy.

    Returns a dict with at least {'url': ...}, plus 'best_back_price'
    and 'best_back_size' when the runner could be matched confidently.
    Returns None if the proxy isn't configured, is unreachable, or
    found no market — callers should treat None as "no button".

    The `user` argument is accepted for forward compatibility with
    per-user credentials (see models.py) but is not yet used — the
    proxy currently always uses the VPS's shared Betfair account.
    """
    proxy_url, secret = _proxy_config()
    if not proxy_url:
        log.info("[BetfairLookup] BETFAIR_PROXY_URL/SECRET not configured - skipping")
        return None

    try:
        resp = requests.get(
            f"{proxy_url}/betfair/market-url",
            params={
                'course': course,
                'time': time_str,
                'horse': horse_name,
                'date': race_date.strftime('%Y-%m-%d'),
            },
            headers={'X-Proxy-Secret': secret},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        log.error(f"[BetfairLookup] Proxy unreachable for {course} {time_str}: {e}")
        return None

    if resp.status_code == 403:
        log.error("[BetfairLookup] Proxy rejected our secret - check that "
                  "BETFAIR_PROXY_SECRET matches the VPS's shared_secret")
        return None
    if resp.status_code != 200:
        log.error(f"[BetfairLookup] Proxy returned HTTP {resp.status_code}")
        return None

    try:
        data = resp.json()
    except ValueError:
        log.error("[BetfairLookup] Proxy returned non-JSON response")
        return None

    if not data.get('url'):
        return None
    return data


def get_betfair_market_url(course, time_str, horse_name, race_date, user=None):
    """Backwards-compatible wrapper returning just the URL string."""
    info = get_betfair_market_info(course, time_str, horse_name, race_date, user)
    return info.get('url') if info else None


def proxy_health():
    """Check whether the proxy is reachable and its Betfair login works.
    Used by /api/betfair-diagnose. Returns a dict describing the state."""
    proxy_url, secret = _proxy_config()
    if not proxy_url:
        return {'configured': False,
                'error': 'BETFAIR_PROXY_URL and/or BETFAIR_PROXY_SECRET not set'}
    try:
        resp = requests.get(f"{proxy_url}/betfair/health", timeout=TIMEOUT_SECONDS)
        return {'configured': True, 'reachable': True,
                'status_code': resp.status_code, 'body': resp.json()}
    except requests.RequestException as e:
        return {'configured': True, 'reachable': False, 'error': str(e)}
    except ValueError:
        return {'configured': True, 'reachable': True,
                'error': 'proxy returned non-JSON from /betfair/health'}
