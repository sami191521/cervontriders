"""Pydantic schemas — the data contract (handoff §2, §9, §10).

Field names match the exact keys the front-end consumes, so the UI needs no
changes. `None` means "column absent in this upload" -> the UI hides the stat
(send null, not 0).
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict

RaceMetric = Literal["sales", "target"]


class Rider(BaseModel):
    """One agent. Raw fields from the Five9 export + server-derived fields."""
    # identity / team / tenure
    n: str                                   # display name
    id: str                                  # slug of name (stable profile key)
    tl: str = "Unassigned"                   # cleaned team
    tlraw: str = ""                          # original team-leader value
    tn: str = ""                             # raw tenure string
    status: str = ""

    # activity
    d: float = 0                             # present days
    th: float = 0                            # total productive hours
    ah: float = 0                            # avg productive hours/day
    pp: float = 0                            # productive %, 0-100
    tw: float = 0                            # total wait minutes
    aw: float = 0                            # avg wait minutes/day

    # the metric
    ld: int = 0                              # SALES = Five9 leads submitted (cumulative)
    r: float = 0                             # sales/day = submitted ratio

    # optional quality / breakdown (null when not in the upload)
    target: Optional[float] = None           # decimal (0.30 = 30%)
    conv: Optional[float] = None             # decimal
    valid: Optional[int] = None
    validpct: Optional[float] = None         # 0-100
    mva: Optional[int] = None
    wc: Optional[int] = None
    pi: Optional[int] = None
    oth: Optional[int] = None

    # any upload columns not mapped to a known field — kept verbatim so the
    # admin can choose to display them ({column name: value as in the file}).
    extra: dict = Field(default_factory=dict)

    # fixed racer bib number (assigned from last month's final standings; stays
    # the same all month regardless of current position). null if unassigned.
    bib: Optional[int] = None

    # server-derived (handoff §2, §5, §8)
    rank: int = 0                            # position by active race metric
    dRank: int = 0                           # rank change since previous upload (+ = climbed)
    dSales: int = 0                          # sales change since previous upload
    cps: int = 0                             # checkpoints cleared (0-20)
    week: int = 0                            # weekly stage score (cumulative - Monday baseline)
    streak: int = 0                          # consecutive days with +5 sales


class TeamStanding(BaseModel):
    team: str
    total: int
    count: int
    avg: float
    color: str


class Jersey(BaseModel):
    """A single award holder (only present when its source data exists)."""
    id: str
    n: str
    stat: str                                # short display value, e.g. "119 sales"


class Jerseys(BaseModel):
    yellow: Optional[Jersey] = None          # GC leader
    green: Optional[Jersey] = None           # sprint pace (sales/day)
    polka: Optional[Jersey] = None           # King of Hustle (productive %)
    white: Optional[Jersey] = None           # top rookie
    sprint: Optional[Jersey] = None          # today's sprint (dSales)
    breakaway: Optional[Jersey] = None       # biggest rank climb (dRank)
    mva: Optional[Jersey] = None
    wc: Optional[Jersey] = None
    pi: Optional[Jersey] = None
    clean: Optional[Jersey] = None           # quality


class Videos(BaseModel):
    intro: str = ""
    stage: str = ""
    finale: str = ""


class Config(BaseModel):
    """Shared board config (handoff §9). Was per-device browser storage."""
    raceMetric: RaceMetric = "sales"
    teamGoal: int = 0
    visibleStats: list[str] = Field(
        default_factory=lambda: ["r", "pp", "ah", "aw", "ld", "cps", "d", "th"]
    )
    videos: Videos = Field(default_factory=Videos)
    lastUpdated: Optional[str] = None        # ISO timestamp of most recent ingest
    # teams pulled out of the main Tour into their own separate race (own
    # leaderboard/podium), e.g. a different campaign. Empty = everyone in one race.
    separateTeams: list[str] = Field(default_factory=list)


class ConfigUpdate(BaseModel):
    """Partial config update (admin) — only provided fields change."""
    raceMetric: Optional[RaceMetric] = None
    teamGoal: Optional[int] = None
    visibleStats: Optional[list[str]] = None
    videos: Optional[Videos] = None
    separateTeams: Optional[list[str]] = None


class CredentialUpdate(BaseModel):
    """Admin changes their own username/password (handoff: real auth server-side)."""
    currentUser: Optional[str] = None
    currentPass: str
    newUser: Optional[str] = None
    newPass: Optional[str] = None


class Profile(BaseModel):
    """Per-rider editable bits, keyed by rider id (handoff §9)."""
    model_config = ConfigDict(extra="ignore")
    nickname: str = ""
    quote: str = ""
    photo: str = ""                          # data-URL today; image URL server-side


class StandingsResponse(BaseModel):
    """Shape returned by GET /api/standings (handoff §10)."""
    lastUpdated: Optional[str] = None
    raceMetric: RaceMetric = "sales"
    race: str = "Tour of Belize"             # which race these standings are for
    scope: str = "all"                       # "all" or a team name
    riders: list[Rider] = Field(default_factory=list)
    teams: list[TeamStanding] = Field(default_factory=list)
    jerseys: Jerseys = Field(default_factory=Jerseys)
    # weekly stage standings: riders with this week's score (cumulative - Monday
    # baseline), ranked by `week` desc. Top 3 win the stage prizes (§8).
    stageWinners: list[Rider] = Field(default_factory=list)
    weekStart: Optional[str] = None          # ISO Monday date the stage began


class IngestSummary(BaseModel):
    """Returned by POST /api/ingest."""
    riders: int
    teams: int
    lastUpdated: str
    newWeek: bool = False
