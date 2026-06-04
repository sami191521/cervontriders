"""SQLite storage — the single source of truth (handoff §8, §9).

Holds what the per-device front-end cannot:
- the current dataset (latest normalized riders)
- previous-upload snapshot (movement: dRank/dSales)
- Monday baseline (weekly stage score, resets each week)
- daily cumulative snapshots (consistency streak)
- shared config + per-rider profiles

Stored values are JSON in a small kv table, plus dedicated tables for the
daily history and profiles.
"""
import os
import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

from . import standings as st
from .models import Config

_DB_PATH = os.environ.get(
    "TOB_DB", os.path.join(os.path.dirname(__file__), "..", "tob.sqlite3")
)


def set_db_path(path: str):
    """Override the DB location (used by tests)."""
    global _DB_PATH
    _DB_PATH = path


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_sales (
                day      TEXT,
                rider_id TEXT,
                ld       INTEGER,
                PRIMARY KEY (day, rider_id)
            );
            CREATE TABLE IF NOT EXISTS profiles (
                id       TEXT PRIMARY KEY,
                nickname TEXT DEFAULT '',
                quote    TEXT DEFAULT '',
                photo    TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ingest_log (
                ts          TEXT PRIMARY KEY,
                riders_json TEXT
            );
            """
        )


# ---------- kv helpers -------------------------------------------------------
def kv_get(key: str, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def kv_set(key: str, value):
    with _conn() as c:
        c.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


# ---------- config -----------------------------------------------------------
def get_config() -> Config:
    raw = kv_get("config")
    return Config(**raw) if raw else Config()


def save_config(cfg: Config):
    kv_set("config", cfg.model_dump())


# ---------- dataset + movement snapshots ------------------------------------
def get_dataset() -> list[dict]:
    """The latest normalized rider dicts (pre-derive). [] if none yet."""
    return kv_get("dataset", []) or []


def get_prev_rank() -> dict:
    return kv_get("prev_rank", {}) or {}


def get_prev_sales() -> dict:
    return kv_get("prev_sales", {}) or {}


def get_week_baseline() -> dict:
    return kv_get("week_baseline", {}) or {}


# ---------- weekly + daily helpers ------------------------------------------
def _monday_of(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def get_streaks() -> dict:
    """Consecutive days (ending most-recent) with a +5 day-over-day gain (§8)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT day, rider_id, ld FROM daily_sales ORDER BY day"
        ).fetchall()
    history: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        history.setdefault(r["rider_id"], []).append((r["day"], r["ld"]))
    streaks = {}
    for rid, seq in history.items():
        s = 0
        for i in range(len(seq) - 1, 0, -1):
            if seq[i][1] - seq[i - 1][1] >= 5:
                s += 1
            else:
                break
        streaks[rid] = s
    return streaks


# ---------- the ingest transaction (handoff §10) ----------------------------
def record_ingest(riders: list[dict], when: Optional[datetime] = None) -> dict:
    """Persist a new upload and update all derived snapshots.

    Order (§10): snapshot previous standings -> roll Monday baseline if a new
    week -> append today's daily snapshot -> store dataset + lastUpdated.
    Returns {"lastUpdated": iso, "newWeek": bool}.
    """
    when = when or datetime.now()
    cfg = get_config()

    # 1. snapshot the *previous* board for movement (dRank/dSales)
    old = get_dataset()
    if old:
        old_res = st.compute_standings(old, race_metric=cfg.raceMetric)
        pr, ps = st.snapshot_ranks(old_res["riders"])
        kv_set("prev_rank", pr)
        kv_set("prev_sales", ps)
    else:
        kv_set("prev_rank", {})
        kv_set("prev_sales", {})

    # 2. weekly stage: roll the Monday baseline when a new week starts
    monday = _monday_of(when.date())
    new_week = kv_get("week_start") != monday
    if new_week:
        # baseline = cumulative totals entering the week (the prior dataset),
        # or this upload's totals if there is no prior data yet.
        source = old if old else riders
        kv_set("week_baseline", {a["id"]: int(a.get("ld") or 0) for a in source})
        kv_set("week_start", monday)

    # 3. daily snapshot for the streak (latest upload of the day wins)
    today = when.date().isoformat()
    with _conn() as c:
        c.executemany(
            "INSERT INTO daily_sales(day,rider_id,ld) VALUES(?,?,?) "
            "ON CONFLICT(day,rider_id) DO UPDATE SET ld=excluded.ld",
            [(today, a["id"], int(a.get("ld") or 0)) for a in riders],
        )

    # 4. store the new dataset + timestamp
    iso = when.isoformat()
    kv_set("dataset", riders)
    kv_set("lastUpdated", iso)
    cfg.lastUpdated = iso
    save_config(cfg)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO ingest_log(ts,riders_json) VALUES(?,?)",
            (iso, json.dumps(riders)),
        )

    # 5. every rider gets a racer bib number (new agents get the next free one)
    bibs = kv_get("bibs", {}) or {}
    new_bibs = st.assign_missing_bibs(bibs, riders, cfg.separateTeams or [])
    if new_bibs != bibs:
        kv_set("bibs", new_bibs)

    return {"lastUpdated": iso, "newWeek": new_week}


# ---------- profiles ---------------------------------------------------------
def get_profile(rider_id: str) -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT nickname,quote,photo FROM profiles WHERE id=?", (rider_id,)
        ).fetchone()
    return dict(row) if row else {"nickname": "", "quote": "", "photo": ""}


def save_profile(rider_id: str, data: dict):
    with _conn() as c:
        c.execute(
            "INSERT INTO profiles(id,nickname,quote,photo) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname,"
            "quote=excluded.quote, photo=excluded.photo",
            (rider_id, data.get("nickname", ""), data.get("quote", ""), data.get("photo", "")),
        )


def all_profiles() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT id,nickname,quote,photo FROM profiles").fetchall()
    return {r["id"]: {"nickname": r["nickname"], "quote": r["quote"], "photo": r["photo"]} for r in rows}
