"""
Shared utilities for Magnolia Horses.
Import from here rather than duplicating across modules.
"""
import re as _re

# ── UK Racecourses ──────────────────────────────────────────────────────────
# GB & Ireland racecourses — used for the "UK only" / "GB & Ireland" filter
UK_COURSES = {
    # Great Britain
    'aintree', 'ascot', 'ayr', 'bangor', 'bath', 'beverley', 'brighton',
    'carlisle', 'cartmel', 'catterick', 'chelmsford', 'cheltenham', 'chepstow',
    'chester', 'doncaster', 'epsom', 'exeter', 'fakenham', 'ffos las',
    'fontwell', 'goodwood', 'hamilton', 'haydock', 'hereford', 'hexham',
    'huntingdon', 'kelso', 'kempton', 'leicester', 'lingfield', 'ludlow',
    'market rasen', 'musselburgh', 'newbury', 'newcastle', 'newmarket',
    'nottingham', 'perth', 'plumpton', 'pontefract', 'redcar', 'ripon',
    'salisbury', 'sandown', 'sedgefield', 'southwell', 'stratford', 'taunton',
    'thirsk', 'towcester', 'uttoxeter', 'warwick', 'wetherby', 'wincanton',
    'windsor', 'wolverhampton', 'worcester', 'yarmouth', 'york',
    # Ireland
    'ballinrobe', 'bellewstown', 'clonmel', 'cork', 'curragh', 'downpatrick',
    'dundalk', 'fairyhouse', 'galway', 'gowran park', 'kilbeggan', 'killarney',
    'laytown', 'leopardstown', 'limerick', 'listowel', 'naas', 'navan',
    'punchestown', 'roscommon', 'sligo', 'thurles', 'tipperary', 'tralee',
    'tramore', 'wexford',
}

def is_uk_course(name):
    """Return True if the course name (with optional suffixes like (AW)) is a UK course."""
    clean = _re.sub(r'\s*\([^)]+\)\s*$', '', (name or '').strip()).lower()
    return clean in UK_COURSES


def strip_country(name):
    """Strip parenthetical suffix e.g. 'Desert Crown (IRE)', 'Newmarket (July)' -> base name."""
    return _re.sub(r'\s*\([^)]+\)\s*$', '', (name or '').strip())
