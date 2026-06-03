"""Static reference data for the Tour of Belize.

Mirrors the front-end's fixed data so the API is the single source of truth:
- ROUTE: the 21 ordered milestones (handoff §7)
- tenure bucketing (handoff §4)
- team colour palette (HTML TEAM_PALETTE)
- checkpoint / stage thresholds (handoff §5, §8)
"""
import re

# --- checkpoint + stage maths (handoff §5, §8) -----------------------------
SALES_PER_CP = 5      # every 5 sales clears a checkpoint
MAX_CP = 20           # 20 checkpoints = full course
STAGE_FINISH = 25     # 25 sales = a weekly stage finish
FULL_COURSE = 100     # 100 sales = Punta Gorda -> Corozal


# --- the route: 21 milestones, south -> north (handoff §7) ------------------
# i, sales, town, district, lat, lng, type, week(finish marker)
_ROUTE_RAW = [
    (0,   0,   "Punta Gorda",      "Toledo",      16.0989, -88.8095, "start",  None),
    (1,   5,   "Forest Home",      "Toledo",      16.1358, -88.8299, "cp",     None),
    (2,   10,  "Big Falls",        "Toledo",      16.2640, -88.8863, "cp",     None),
    (3,   15,  "Independence",     "Stann Creek", 16.5349, -88.4196, "cp",     None),
    (4,   20,  "Hopkins Junction", "Stann Creek", 16.8535, -88.2814, "cp",     None),
    (5,   25,  "Dangriga",         "Stann Creek", 16.9602, -88.2210, "finish", 1),
    (6,   30,  "Middlesex",        "Stann Creek", 17.0397, -88.5203, "cp",     None),
    (7,   35,  "St. Margaret's",   "Cayo",        17.0895, -88.6123, "cp",     None),
    (8,   40,  "Belmopan",         "Cayo",        17.2523, -88.7641, "cp",     None),
    (9,   45,  "Roaring Creek",    "Cayo",        17.2601, -88.7999, "cp",     None),
    (10,  50,  "San Ignacio",      "Cayo",        17.1508, -89.0749, "finish", 2),
    (11,  55,  "Unitedville",      "Cayo",        17.2121, -88.9378, "cp",     None),
    (12,  60,  "Blackman Eddy",    "Cayo",        17.2227, -88.9260, "cp",     None),
    (13,  65,  "Teakettle",        "Cayo",        17.2258, -88.8530, "cp",     None),
    (14,  70,  "Hattieville",      "Belize",      17.4504, -88.3961, "cp",     None),
    (15,  75,  "Belize City",      "Belize",      17.5046, -88.1962, "finish", 3),
    (16,  80,  "Burrell Boom",     "Belize",      17.5708, -88.4137, "cp",     None),
    (17,  85,  "Sand Hill",        "Belize",      17.6378, -88.3695, "cp",     None),
    (18,  90,  "Orange Walk Town", "Orange Walk", 18.0802, -88.5614, "cp",     None),
    (19,  95,  "Ranchito",         "Corozal",     18.3809, -88.4101, "cp",     None),
    (20,  100, "Corozal Town",     "Corozal",     18.4031, -88.3968, "final",  4),
]

ROUTE = [
    {
        "i": i, "sales": sales, "town": town, "district": district,
        "lat": lat, "lng": lng, "type": typ, "week": week,
    }
    for (i, sales, town, district, lat, lng, typ, week) in _ROUTE_RAW
]

# town names only, indexed by checkpoint (MILES in the HTML)
MILES = [m["town"] for m in ROUTE]


def checkpoints_for(race_value: float) -> int:
    """Checkpoints cleared = min(floor(value/5), 20)  (handoff §5)."""
    return min(int(race_value // SALES_PER_CP), MAX_CP)


def stage_of_sales(sales: float) -> int:
    """Which weekly stage a cumulative-sales total sits in (1..4)."""
    if sales <= 0:
        return 1
    return min(int((sales - 1) // STAGE_FINISH) + 1, 4)


# --- tenure buckets (handoff §4 / HTML TEN_DEFS) ---------------------------
# order matters: first match wins
_TENURE_DEFS = [
    ("vet",    "Veteran", re.compile(r"greater than 90|91-120|121-180|greater than 180|90\+", re.I), False),
    ("pro",    "Pro",     re.compile(r"61-90", re.I), False),
    ("rising", "Rising",  re.compile(r"31-60", re.I), False),
    ("rookie", "Rookie",  re.compile(r"0-30", re.I), True),
]


def tenure_info(tn) -> dict:
    """Bucket a raw tenure string. Returns {code, label, rookie}."""
    s = str(tn or "")
    for code, label, rx, rookie in _TENURE_DEFS:
        if rx.search(s):
            return {"code": code, "label": label, "rookie": rookie}
    return {"code": "other", "label": s or "—", "rookie": False}


# --- team colours (HTML TEAM_PALETTE) --------------------------------------
TEAM_PALETTE = [
    "#4361ee", "#e5383b", "#2a9d8f", "#f4a300", "#9b5de5",
    "#0096c7", "#ff7d00", "#588157", "#d4a373", "#6d597a",
]


def assign_team_colors(team_totals: dict) -> dict:
    """Colour teams by total sales desc, cycling the palette (HTML recompute)."""
    order = sorted(team_totals.keys(), key=lambda t: -team_totals[t])
    return {t: TEAM_PALETTE[i % len(TEAM_PALETTE)] for i, t in enumerate(order)}
