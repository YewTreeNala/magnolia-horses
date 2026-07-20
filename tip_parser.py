"""
tip_parser.py
=============
Parses raw Turn Of Foot Telegram messages into structured tip dicts.

Each tip dict contains:
    horse_name, course, race_time, bet_type, stake_pts, odds,
    odds_dec, each_way_places, reasoning, uncertain, uncertain_reason

Returns a list of tips (a single message can contain multiple horses).
Non-tip messages (commentary, voice notes, photos) return [].
"""

import re
from datetime import datetime


# ── Odds conversion ───────────────────────────────────────────────────────────

def fractional_to_decimal(frac_str):
    """Convert '8/1' → 9.0, '11/2' → 6.5 etc."""
    try:
        parts = frac_str.strip().split('/')
        if len(parts) == 2:
            num, den = float(parts[0]), float(parts[1])
            return round(num / den + 1, 4)
    except Exception:
        pass
    return 0.0


# ── Course name normalisation ──────────────────────────────────────────────────

COURSE_ALIASES = {
    'newmarket': 'Newmarket', 'ascot': 'Ascot', 'royal ascot': 'Ascot',
    'goodwood': 'Goodwood', 'york': 'York', 'chester': 'Chester',
    'epsom': 'Epsom', 'sandown': 'Sandown', 'haydock': 'Haydock',
    'newbury': 'Newbury', 'windsor': 'Windsor', 'kempton': 'Kempton',
    'lingfield': 'Lingfield', 'wolverhampton': 'Wolverhampton',
    'chelmsford': 'Chelmsford', 'nottingham': 'Nottingham',
    'leicester': 'Leicester', 'carlisle': 'Carlisle', 'catterick': 'Catterick',
    'ripon': 'Ripon', 'thirsk': 'Thirsk', 'beverley': 'Beverley',
    'redcar': 'Redcar', 'pontefract': 'Pontefract', 'doncaster': 'Doncaster',
    'southwell': 'Southwell', 'bath': 'Bath', 'brighton': 'Brighton',
    'chepstow': 'Chepstow', 'ffos las': 'Ffos Las', 'warwick': 'Warwick',
    'stratford': 'Stratford', 'worcester': 'Worcester', 'uttoxeter': 'Uttoxeter',
    'market rasen': 'Market Rasen', 'huntingdon': 'Huntingdon',
    'plumpton': 'Plumpton', 'wincanton': 'Wincanton', 'taunton': 'Taunton',
    'exeter': 'Exeter', 'hereford': 'Hereford', 'ludlow': 'Ludlow',
    'sedgefield': 'Sedgefield', 'ayr': 'Ayr', 'hamilton': 'Hamilton',
    'musselburgh': 'Musselburgh', 'perth': 'Perth',
    'curragh': 'Curragh', 'leopardstown': 'Leopardstown',
    'limerick': 'Limerick', 'galway': 'Galway', 'navan': 'Navan',
    'naas': 'Naas', 'cork': 'Cork', 'tipperary': 'Tipperary',
    'dundalk': 'Dundalk', 'killarney': 'Killarney', 'listowel': 'Listowel',
    'deauville': 'Deauville', 'longchamp': 'Longchamp',
    'newcastle': 'Newcastle', 'chester': 'Chester',
}

def normalise_course(name):
    return COURSE_ALIASES.get(name.strip().lower(), name.strip().title())


# ── Message classification ────────────────────────────────────────────────────

NON_TIP_PATTERNS = [
    r'^\[voice',
    r'^\[video',
    r'^\[photo',
    r'^no bets',
    r'^ditto',
    r'^morning',
    r'^good morning',
    r'^hi all',
    r'^update at',
    r'^another update',
    r'^bear with',
    r'^appreciate the',
    r'^such fine margins',
    r'^that one hurts',
    r'^jesus',
    r'^\d+ raised',
    r'^£\d+',
    r'^total now',
    r"^i'?m staying",
    r'^i think',
    r'^we got',
    r'^we had',
    r'^it was',
    r'^that.*(?:hurts|painful)',
    r'^catch you',
    r'^keep kicking',
    r'^over & out',
    r'^enjoy your',
    r'^apologies',
    r'^as a reminder',
    r'^there will be',
    r'^there are',
    r'^not our comfort',
    r'^power blue jumps',
    r'^spicy marg',
    r'^nicely',
    r'^a lot of small',
    r'^it.s been poor',
    r'^i had a',
    r'^i.m going',
    r'^summer update',
    r'^silca bay',
    r'^paddy the squire',
    r'^del maro',
    r'^oceans four',
    r'^andesite',
    r'^asset has',
    r'^saffie was',
    r'^greetings',
    r'^cracking effort',
]

def is_non_tip(text):
    if not text or text.startswith('['):
        return True
    low = text.strip().lower()
    for pat in NON_TIP_PATTERNS:
        if re.match(pat, low):
            return True
    # Messages with no odds pattern are not tips
    if not re.search(r'\d+/\d+', text):
        return True
    return False


# ── Section header parsing ────────────────────────────────────────────────────

def extract_header(text):
    """
    Extract the course and optional date from message headers like:
        'Royal Ascot (Part 1)'
        'Newmarket'
        'York'
        'Ascot'
    Returns (course_str, date_str_or_None)
    """
    lines = text.strip().split('\n')
    for i, line in enumerate(lines[:4]):
        line = line.strip()
        # Skip greeting lines
        if any(line.lower().startswith(k) for k in ['morning', 'hi', 'good', 'update', 'bear', 'another']):
            continue
        # Match a line that's just a course name (possibly with Part N)
        clean = re.sub(r'\s*\(Part\s*\d+\)', '', line, flags=re.IGNORECASE).strip()
        if clean.lower() in COURSE_ALIASES or clean.title() in COURSE_ALIASES.values():
            return normalise_course(clean), None
        # Match "Royal Ascot" style
        if re.match(r'^[A-Z][A-Za-z\s]+$', clean) and 2 <= len(clean.split()) <= 4:
            normed = normalise_course(clean)
            if normed != clean.title() or clean.lower() in COURSE_ALIASES:
                return normed, None
    return None, None


# ── Tip line parsing ──────────────────────────────────────────────────────────

# Pattern: time - horse(s) stake bet_type odds [with N places]
# e.g. "3.05 - God Given Talent 0.5pt E/w 33/1 with 4 places"
# e.g. "5.35 - Ghostwriter, Royal Rhyme 0.5pt E/w 9/1, 12/1 with 4 places"
# e.g. "6.10 - Controlla 1pt win 4/1"

TIP_LINE_RE = re.compile(
    r'^(\d{1,2}[:.]\d{2})\s*[-–]\s*'      # time (group 1)
    r'([\w\s\',]+?)\s+'                     # horse name(s) (group 2)
    r'(\d+(?:\.\d+)?)\s*pt\s*'             # stake (group 3)
    r'(e/?w|ew|win)\s*'                     # bet type (group 4)
    r'([\d/]+(?:,\s*[\d/]+)*)'             # odds (group 5, comma-separated for multiple)
    r'(?:\s+with\s+(\d+)\s+places?)?',     # places (group 6, optional)
    re.IGNORECASE
)


def normalise_time(t):
    """Convert '3.05' or '3:05' to '3:05'"""
    return t.replace('.', ':')


def parse_horses_and_odds(horses_str, odds_str):
    """
    Parse potentially multiple horses and their corresponding odds.
    'Ghostwriter, Royal Rhyme' + '9/1, 12/1' → [('Ghostwriter','9/1'), ('Royal Rhyme','12/1')]
    'God Given Talent' + '33/1' → [('God Given Talent','33/1')]
    """
    horses = [h.strip().strip("'") for h in horses_str.split(',') if h.strip()]
    odds_list = [o.strip() for o in odds_str.split(',') if o.strip()]

    pairs = []
    for i, horse in enumerate(horses):
        odds = odds_list[i] if i < len(odds_list) else (odds_list[-1] if odds_list else '')
        pairs.append((horse, odds))
    return pairs


def parse_tip_line(line, current_course=None):
    """
    Parse a single tip line. Returns list of tip dicts (multiple for multi-horse lines).
    Returns [] if line doesn't match tip pattern.
    """
    line = line.strip()
    m = TIP_LINE_RE.match(line)
    if not m:
        return []

    race_time = normalise_time(m.group(1))
    horses_raw = m.group(2).strip()
    stake_pts = float(m.group(3))
    bet_raw = m.group(4).lower().replace('/', '')
    bet_type = 'ew' if 'ew' in bet_raw or 'e' in bet_raw else 'win'
    odds_raw = m.group(5)
    places = int(m.group(6)) if m.group(6) else (0 if bet_type == 'win' else 4)

    pairs = parse_horses_and_odds(horses_raw, odds_raw)

    tips = []
    for horse_name, odds_frac in pairs:
        if not horse_name or len(horse_name) < 2:
            continue
        tips.append({
            'horse_name':         horse_name,
            'course':             current_course or '',
            'race_time':          race_time,
            'bet_type':           bet_type,
            'stake_pts':          stake_pts,
            'odds':               odds_frac,
            'odds_dec':           fractional_to_decimal(odds_frac),
            'each_way_places':    places,
            'each_way_fraction':  5,
            'reasoning':          '',
            'uncertain':          False,
            'uncertain_reason':   '',
        })
    return tips


# ── Full message parser ───────────────────────────────────────────────────────

def parse_message(text, msg_datetime=None):
    """
    Parse a full Telegram message into a list of tip dicts.
    Returns [] for non-tip messages.
    Returns tips with uncertain=True for messages that look like tips
    but couldn't be fully parsed.
    """
    if not text:
        return []

    text = text.strip()

    if is_non_tip(text):
        return []

    lines = text.split('\n')
    current_course, _ = extract_header(text)

    # Try to find a course from the message header lines
    for line in lines[:5]:
        line = line.strip()
        clean = re.sub(r'\s*\(Part\s*\d+\)', '', line, flags=re.IGNORECASE).strip()
        normed = normalise_course(clean)
        if normed.lower() in [v.lower() for v in COURSE_ALIASES.values()]:
            current_course = normed
            break

    tips = []
    current_tip_lines = []   # accumulate reasoning lines after a tip line
    current_tips_in_block = []

    def flush_reasoning():
        if current_tips_in_block and current_tip_lines:
            reasoning = '\n'.join(current_tip_lines).strip()
            for t in current_tips_in_block:
                t['reasoning'] = reasoning

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Check for a new course header mid-message (e.g. multi-course messages)
        clean_line = re.sub(r'\s*\(Part\s*\d+\)', '', line_stripped, flags=re.IGNORECASE).strip()
        normed = normalise_course(clean_line)
        if (normed.lower() in [v.lower() for v in COURSE_ALIASES.values()]
                and not re.search(r'\d+/\d+', line_stripped)
                and len(clean_line.split()) <= 4):
            flush_reasoning()
            current_tip_lines = []
            current_tips_in_block = []
            current_course = normed
            continue

        # Try parsing as a tip line
        parsed = parse_tip_line(line_stripped, current_course)
        if parsed:
            flush_reasoning()
            current_tip_lines = []
            current_tips_in_block = parsed
            tips.extend(parsed)
            continue

        # Otherwise it's reasoning text — accumulate for current tips
        if current_tips_in_block:
            # Skip separator lines
            if re.match(r'^[_\-=]{3,}$', line_stripped):
                flush_reasoning()
                current_tip_lines = []
                current_tips_in_block = []
            else:
                current_tip_lines.append(line_stripped)

    # Flush final reasoning block
    flush_reasoning()

    # Mark as uncertain if we found odds in the message but no parsed tips
    if not tips and re.search(r'\d+/\d+', text):
        return [{
            'horse_name':       '',
            'course':           current_course or '',
            'race_time':        '',
            'bet_type':         'ew',
            'stake_pts':        0.5,
            'odds':             '',
            'odds_dec':         0.0,
            'each_way_places':  4,
            'each_way_fraction': 5,
            'reasoning':        text,
            'uncertain':        True,
            'uncertain_reason': 'Could not parse tip line — manual review needed',
        }]

    return tips


# ── P&L settlement ────────────────────────────────────────────────────────────

def settle_tip(tip, position_str, sp_dec):
    """
    Calculate P&L for a tip given the finishing position and SP.

    Returns dict with: result_type, win_pts, place_pts, total_pts

    For E/W bets:
    - Win part: stake_pts at odds (profit = stake * odds_dec-1) or -stake_pts
    - Place part: stake_pts at (1/fraction * odds) or -stake_pts
      Placed if position <= each_way_places

    For Win bets:
    - stake_pts at odds or -stake_pts
    """
    try:
        pos = int(position_str)
    except (ValueError, TypeError):
        # Non-numeric position (F, PU, UR etc.) = loss
        pos = 99

    stake = tip.stake_pts
    bet_type = tip.bet_type
    places = tip.each_way_places
    fraction = tip.each_way_fraction or 5

    # Use SP decimal if tip odds not reliable
    if sp_dec and sp_dec > 1.0:
        odds_dec = sp_dec
    elif tip.odds_dec and tip.odds_dec > 1.0:
        odds_dec = tip.odds_dec
    else:
        odds_dec = sp_dec or 1.0

    win_pts = 0.0
    place_pts = 0.0

    if bet_type == 'win':
        if pos == 1:
            win_pts = round(stake * (odds_dec - 1), 4)
            result_type = 'win'
        else:
            win_pts = -stake
            result_type = 'loss'
        total_pts = win_pts

    else:  # each-way
        place_odds_dec = round((odds_dec - 1) / fraction + 1, 4)

        # Win part
        if pos == 1:
            win_pts = round(stake * (odds_dec - 1), 4)
            result_type = 'win'
        else:
            win_pts = -stake

        # Place part
        if pos <= places and pos >= 1:
            place_pts = round(stake * (place_odds_dec - 1), 4)
            if result_type != 'win':
                result_type = 'place'
        else:
            place_pts = -stake
            if result_type != 'win':
                result_type = 'loss'

        total_pts = round(win_pts + place_pts, 4)

    return {
        'result_type': result_type,
        'win_pts':     win_pts,
        'place_pts':   place_pts,
        'total_pts':   total_pts,
    }
