"""
Shared utilities for Magnolia Horses.
Import from here rather than duplicating across modules.
"""
import re as _re

# ── UK Racecourses ──────────────────────────────────────────────────────────
UK_COURSES = {
    'ascot', 'ayr', 'bath', 'beverley', 'brighton', 'carlisle', 'catterick',
    'chelmsford', 'cheltenham', 'chepstow', 'chester', 'doncaster', 'epsom',
    'exeter', 'ffos las', 'fontwell', 'goodwood', 'hamilton', 'haydock',
    'hereford', 'hexham', 'huntingdon', 'kempton', 'leicester', 'lingfield',
    'ludlow', 'market rasen', 'musselburgh', 'newbury', 'newcastle',
    'newmarket', 'nottingham', 'perth', 'plumpton', 'pontefract', 'redcar',
    'ripon', 'salisbury', 'sandown', 'sedgefield', 'southwell', 'stratford',
    'taunton', 'thirsk', 'uttoxeter', 'warwick', 'wetherby', 'wincanton',
    'windsor', 'wolverhampton', 'worcester', 'yarmouth', 'york',
    'bangor', 'kelso',
}

def is_uk_course(name):
    """Return True if the course name (with optional suffixes like (AW)) is a UK course."""
    clean = _re.sub(r'\s*\([^)]+\)\s*$', '', (name or '').strip()).lower()
    return clean in UK_COURSES


def strip_country(name):
    """Strip parenthetical suffix e.g. 'Desert Crown (IRE)', 'Newmarket (July)' -> base name."""
    return _re.sub(r'\s*\([^)]+\)\s*$', '', (name or '').strip())
