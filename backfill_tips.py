"""
backfill_tips.py
================
Sends the 6-month Turn Of Foot message history to the Magnolia Horses
backfill endpoint.

Usage: python backfill_tips.py
Requires: requests, the MAGNOLIA_WEBHOOK_URL and MAGNOLIA_WEBHOOK_SECRET
          in config.ini (same section as tipster config).
"""
import requests
import json
from config import _cfg

BASE_URL       = _cfg.get('tipster', 'magnolia_base_url', fallback='https://magnoliahorses.com')
WEBHOOK_SECRET = _cfg.get('tipster', 'webhook_secret', fallback='')

# ── Paste the full message history here ──────────────────────────────────────
# Format: list of dicts with 'datetime', 'text', optional 'race_date'
# datetime format: 'YYYY-MM-DD HH:MM:SS'
# race_date: the date the race actually runs (usually same day as tip for morning posts)

MESSAGES = [
    # June 16 2026
    {"datetime": "2026-06-16 08:31:00", "race_date": "2026-06-16", "text": """Ascot (today)

3.05 - God Given Talent 0.5pt E/w 33/1 with 4 places

Another for the Coventry at a big price. God Given Talent hit the line well at Newbury despite being both green and unfancied in the market. He posted a good time and is reported to have come on leaps and bounds since then. With Buick booked, he's an interesting outsider for sure.

5.00 - Bahadur 0.5pt E/w 20/1 with 5 places

This is a much tougher assignment than his Goodwood win last time out, but he comes out well on all three sets of ratings I refer to and is a big E/w play as such, if he gets home over the additional 4f distance. As is the case for many of these, stamina is the biggest risk factor over a quirky trip for the Flat.

5.35 - Ghostwriter, Royal Rhyme 0.5pt E/w 9/1, 12/1 with 4 places

Ghostwriter comes here fresh but did have a racecourse gallop recently. He's reported to be in fine form and boasts some of the best form on offer here. Should run a big race. Royal Rhyme was also entered in the Prince of Wales's, but has his sights lowered to a more realistic level for this Listed contest. He's had two nice tune-up runs and needs to kick on now. If he can improve on those two efforts, he too is right in the picture for Karl Burke.

6.10 - Paddy The Squire 0.5pt E/w 28/1 with 5 places

A big, lazy sort who has climbed the middle distance ranks well of the past 12 months and he could have more to offer yet. His main aim is the Ebor, but he caught the eye at York over an inadequate trip last time out and he should be sharper for that outing here. Nicely drawn in stall 4, I'm hoping he can nestle into a nice position early, settle well, and give a fair account of himself when the pressure comes on into the home straight."""},

    # June 17 2026 Part 1
    {"datetime": "2026-06-17 08:29:00", "race_date": "2026-06-17", "text": """Royal Ascot (Part 1)

2.30 - Alta Regina 0.5pt E/w 13/2 with 4 places

She's been well backed but looks sure to run a big run from a plumb high draw on figures and appearance, she looks the ideal type of the race. It's a very hot renewal with an endless stream of contenders, but I'm hopeful Alta Regina has the touch of quality needed to separate herself from the majority of these quick fillies.

3.05 - Del Maro 0.5pt E/w 14/1

Del Maro is a good looking son of Camelot who shapes as though the step up to 1m6f could really suit. He brings a good blend of form and experience to the table, coming out nicely on all three ratings I use and he's an agreeable price from a nice draw with Will Buick doing the steering.

4.20 - Almaqam 0.5pt E/w 7/1

A good winner at the Curragh for us last time out. This is a significantly tougher assignment, but Bay City Roller franked the form nicely at Epsom and Ed Walker's gelding has done very little wrong. He's a pound or two below the best of Ombudsman and the French lad, but he's going the right way and I'd expect another big run today."""},

    # June 17 2026 Part 2
    {"datetime": "2026-06-17 09:37:00", "race_date": "2026-06-17", "text": """Royal Ascot (Part 2)

5.00 - Checkandchallenge 0.5pt E/w 20/1 with 6 places

I've had this fella on my mind for the Hunt Cup since his eye-catching run at Newbury two starts back. I've followed him for a while and still feel he's capable of landing a big one some day. He wears cheekpieces here which can help, he's well drawn and hopefully all goes well in the pre-lims etc. All being well he can run on strongly late to hit the frame.

5.35 - American Gal 0.5pt E/w 33/1 with 5 places

American Gal ran no race at Newmarket and her form figures don't inspire confidence, but most of her runs have been at a much higher grade and she makes her handicap debut off a nice mark of 97. She's gone well at Ascot before and is a big enough price to take a chance on.

6.10 - Controlla 1pt win 4/1

Her RPR/RP Topspeed combo of 96/94 stands out like a sore thumb here. Her experience over 6f is valuable and she ticks all the boxes for a win bet in a field lacking depth. There's 3-4 horses that could improve and we also have to hope Controlla handles the occasion and the track. But if the stars align, she's going to be very hard to beat."""},

    # June 18 2026
    {"datetime": "2026-06-18 08:29:00", "race_date": "2026-06-18", "text": """Ripon

2.15 - State Of Gold 0.5pt E/w 11/2

This filly gets a bit of weight from the boys and posted a good set of numbers at York, which entitle her to be involved at the finish for this 6f novice.

Southwell

7.30 - Sheikhnshah 0.5pt E/w 10/1

Centigrade has been off for over 600 days and carries a big weight here. I'm hopeful Sheikhshah is at a nice price point at 10/1.

Leopardstown

7.50 - Eniac 0.5pt E/w 7/1

Changing Lanes sets a fair bar but Eniac is right on collateral form terms with the favourite on a line through Bay Of Stars."""},

    # June 18 2026 Royal Ascot
    {"datetime": "2026-06-18 09:41:00", "race_date": "2026-06-18", "text": """Royal Ascot

2.30 - Aperoll 0.5pt E/w 11/1 with 4 places

Quite an open and interesting renewal of the Chesham here, where Richard Hannon's Aperoll has bright E/w claims after a good win at Newbury on debut.

3.05 - Golden Knight, Joulany 0.5pt E/w 12/1, 14/1 with 5 places

Golden Knight is very interesting here off a mark of 87. His win at Newmarket has worked out seriously well. Joulany comes up well on the clock here and is going the right way.

4.15 - Rahiebb 0.5pt E/w 13/2

It was a nice performance in the Yorkshire Cup last time out and Rahiebb looks to me like he'll stay all day.

4.50 - Crest Of Fire, Wise Prince 0.5pt E/w 25/1, 40/1 with 6 places

Crest Of Fire stayed on well late in the day at Carlisle. Wise Prince is a lovely looking horse who steps down in class for this handicap debut.

5.35 - Oceans Four 0.5pt E/w 100/1

Shooting for the moon a bit here but Oceans Four is on my list of horses to follow this season.

6.10 - Royal Velvet 0.5pt E/w 16/1 with 5 places

Love this mare, she's been a good servant to the Mainline and arrives here in seriously good order."""},

    # June 19 2026
    {"datetime": "2026-06-19 08:29:00", "race_date": "2026-06-19", "text": """Ascot

2.30 - Libertango, Dark Issue 0.5pt E/w 10/1, 16/1 with 4 places

Sun Goddess arrives with a standout RPR of 92, which puts her a handful of pounds ahead of these on form numbers.

4.20 - Touleen 0.5pt E/w 10/1

It will be difficult to penetrate the O'Brien fillies, but Touleen is the only other in the field with the right blend of performance numbers.

5.00 - Rosa Inglesa, True Test 0.5pt E/w 12/1, 40/1 with 6 places

Rosa Inglesa gets in here off a low weight after quickening up stylishly to score last time out.

Limerick

4.47 - Expert Dancer 1pt win 3/1

Horses can always improve on a whim in this sphere, but based on all we know, 3/1 underestimates Expert Dancer's chances of winning this 7f maiden."""},

    # June 19 Saturday tips
    {"datetime": "2026-06-19 18:51:00", "race_date": "2026-06-20", "text": """Royal Ascot

2.30 - Force Noir 0.5pt E/w 11/1 with 4 places

3.40 - Comanche Brave 0.5pt E/w 14/1 with 4 places

4.20 - Thesecretadversary, Andab 0.5pt E/w 10/1, 22/1 with 4 places

5.00 - Ten Pounds, Sondad, Far Above Dream 0.5pt E/w 11/1, 18/1, 18/1 with 6 places

Newmarket

2.36 - Alfaraz 1pt win 7/2

Redcar

1.42 - Furturra 1pt win 2/1

2.12 - Undercover Affair 1pt win 4/1

Doncaster

5.48 - Cash Cove 0.5pt E/w 7/1"""},

    # June 25 2026
    {"datetime": "2026-06-25 08:33:00", "race_date": "2026-06-25", "text": """Newcastle

4.22 - Aura Of Melania 0.5pt E/w 11/1

Improved nicely from her first run and with a bit more progression here she comes up well enough on the numbers to hit the frame.

Newmarket

12.15 - The Can Can Queen 0.5pt E/w 33/1

She may well be outclassed but she'll certainly appreciate the step back up to 6f today."""},

    # June 25 Saturday tips
    {"datetime": "2026-06-25 18:01:00", "race_date": "2026-06-26", "text": """Newcastle

2.10 - Heavenly Heather 0.5pt E/w 11/1 with 4 places

Ran to an RPR of 116 at Royal Ascot and if she can repeat that in any fashion here she won't be miles away.

3.15 - Zanndabad, Align The Stars 0.5pt E/w 14/1, 18/1 with 5 places

Both have interesting profiles for the race and are in my notebook.

York

1.55 - Andesite, Frankies Dream 0.5pt E/w 8/1 apiece with 5 places

Andesite was an eyecatcher here last time out. Frankie is consistent and looks ahead of the handicapper.

2.25 - Our Cody 0.5pt E/w 9/1 with 4 places

Tricky to win with clearly, but has a bit of talent behind the form figures.

Curragh

3.20 - Velozee 0.5pt E/w 17/2 with 5 places

Ran well at Ascot and wasn't given a hard time.

3.55 - Venetian Lace 0.5pt E/w 14/1

Ground went against her and we learned nothing at Epsom. Back to 1m2f is more within range."""},

    # July 2 2026
    {"datetime": "2026-07-02 23:33:00", "race_date": "2026-07-03", "text": """Chepstow

6.15 - Deadline 0.5pt E/w 13/2

Doncaster

2.00 - Mobadir 0.5pt E/w 22/1

Sandown

2.25 - Bill The Bull 0.5pt E/w 5/1

3.00 - Cilician 1pt win 5/2

3.35 - Royal Rhyme 1pt win 11/2

5.15 - Silca Bay, H Key Lails 0.5pt E/w 10/1, 25/1"""},

    # July 3 evening
    {"datetime": "2026-07-03 19:58:00", "race_date": "2026-07-04", "text": """Sandown

2.25 - Ebt's Guard, Bourbon Blues 0.5pt E/w 12/1, 20/1 5 places

Newmarket

3.15 - Paddy The Squire 0.5pt E/w 15/2 with 4 places"""},

    # July 9 2026
    {"datetime": "2026-07-09 09:14:00", "race_date": "2026-07-09", "text": """Newmarket

3.00 - Calico Blue, Ten Carat Harry 0.5pt E/w 15/2, 11/1 with 4 places

Both ran in the same race at Royal Ascot and are interesting for different reasons.

4.10 - Tall Trees 0.5pt E/w 15/2

Tall Trees was an eyecatcher at Royal Ascot when running on late behind our nice winner Libertango.

Leopardstown

7.03 - Lord Massusus 0.5pt E/w 25/1 with 4 places

This experienced grey comes out well on all three sets of numbers I refer to.

Newbury

5.45 - My Normandie 0.5pt E/w 10/1

May be seen to better effect once handicapping but I am led by numbers and evidence.

Epsom

7.50 - Balon D'or 0.5pt E/w 16/1

16/1 seems a big price with the three places provided the dead eight remain."""},

    # July 9 evening
    {"datetime": "2026-07-09 18:28:00", "race_date": "2026-07-10", "text": """Newmarket

3.00 - Roaring Legend 0.5pt E/w 40/1

Strong stayer Roaring Legend isn't out of the place picture.

3.35 - Venetian Lace 0.5pt E/w 25/1

The Oaks project didn't work out, but she posted a quick time in the Guineas.

4.45 - Tatterstall 0.5pt E/w 8/1 with 4 places

Tatterstall went off the boil a bit but he's been back to something like his best in recent outings.

York

2.10 - Andesite 0.5pt E/w 5/1 with 5 places

Andesite looked like a horse ready to land a good pot or two last time out.

2.45 - America Queen, Hold A Dream 0.5pt E/w 9/1, 50/1 with 4 places

Spicy Marg is a solid favourite but I think race conditions are going to suit America Queen."""},

    # July 10 Saturday
    {"datetime": "2026-07-10 12:58:00", "race_date": "2026-07-12", "text": """Newmarket (Sat)

4.00 - Pikachu 1pt win 9/1

Pikachu ran to an RPR & RP Topspeed combination of 98/89 when 5th in the Chesham at Royal Ascot.

4.35 - Big Mojo 0.5pt E/w 10/1

It looks a tremendous renewal of the July Cup. I'm willing to give Big Mojo another chance to shine.

York (Sat)

2.39 - Checkandchallenge 0.5pt E/w 12/1 with 4 places

You might be thinking when are we getting off this Checkandchallenge ride?

3.12 - Heavenly Heather 0.5pt E/w 15/2

She was a non-runner the other day in a slightly tougher race, though this is an open and quality affair too.

3.45 - Castle Stuart 0.5pt E/w 16/1 with 5 places

Castle Stuart looks nicely weighted for this contest on the back of an excellent course and distance effort last time."""},

    # July 13 2026
    {"datetime": "2026-07-13 08:55:00", "race_date": "2026-07-13", "text": """Windsor

5.50 - Asset 0.5pt E/w 7/1

Asset has eased from 4/1 to 7/1. In receipt of 5lb and 10lb respectively, her performance numbers put her bang there with them."""},

    # July 15 2026
    {"datetime": "2026-07-15 08:44:00", "race_date": "2026-07-15", "text": """Bath

3.01 - Sheer Beauty 0.5pt E/w 7/1

Sheer Beauty is a nippy filly who shaped quite well for a long way at Windsor and Bath could very much be to her liking.

Killarney

7.00 - Wild Bessie 0.5pt E/w 50/1 with 4 places

A hot 12-runner Listed race. Wild Bessie comes out quite favourably on the clock."""},

    # July 18 2026
    {"datetime": "2026-07-18 08:29:00", "race_date": "2026-07-18", "text": """Newbury

3.37 - Vollering 0.5pt E/w 8/1 with 5 places

Vollering is a quick and experienced 2yo who wears her heart on her sleeve from the front.

Market Rasen

2.10 - Morning Mayhem, Howth 0.5pt E/w 25/1, 40/1 with 4 places

I avoid jumps like the plague at this time of year but it's not too often you get a chance in a valuable race.

Curragh

3.25 - Cover Up 0.5pt E/w 8/1

Veteran Cover Up was an eye-catcher behind Mission Control at Royal Ascot.

Newmarket

3.05 - Little Dorrit 0.5pt E/w 9/1

Her one start so far this season was a promising effort and she's entitled to come on a bundle."""},

    # July 18 update
    {"datetime": "2026-07-18 10:48:00", "race_date": "2026-07-18", "text": """Newbury

1.55 - Ocean's Four 0.5pt E/w 6/1

He ran a big one for us at huge odds the last day and any improvement on that would see him go close.

Newmarket

4.17 - Generous Rascal 0.5pt E/w 18/1

The return to 6f is a positive and he's becoming nicely handicapped in this sort of grade.

5.20 - Brisk Symphony 0.5pt E/w 8/1

None of these look particularly well handicapped and I'm hopeful Brisk Symphony can produce something more like her Lingfield and Yarmouth runs.

Ripon

5.03 - Mohmentous 1pt win 9/2

Mohmentous stands out quite a bit on RP speed figures here and looks a big price at 9/2."""},

    # July 19 2026
    {"datetime": "2026-07-19 08:32:00", "race_date": "2026-07-19", "text": """Curragh

3.15 - Power Blue 1pt win 2/1 generally

Power Blue jumps out at every touch point. A Group 1 winner over 6f at the track, he's competed at the same level over a mile in recent outings, producing superior figures to this field."""},
]


def run_backfill():
    url = f"{BASE_URL}/api/admin/backfill-tips"
    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Secret': WEBHOOK_SECRET,
    }
    # Need to be logged in as admin — use session cookie
    # For simplicity, call with a session. Admin must be logged in separately.
    # Better: use the webhook endpoint per message
    print(f"Sending {len(MESSAGES)} messages to {url}")

    # Send as bulk backfill
    resp = requests.post(url, json={'messages': MESSAGES}, headers=headers, timeout=60)
    if resp.status_code == 200:
        result = resp.json()
        print(f"Done: {result}")
    else:
        print(f"Error {resp.status_code}: {resp.text[:500]}")


if __name__ == '__main__':
    run_backfill()
