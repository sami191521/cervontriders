"""Supabase (hosted Postgres) storage — same interface as db.py.

Talks to Supabase's PostgREST API over HTTPS with httpx (no extra SDK). Used
when SUPABASE_URL + SUPABASE_KEY are set; otherwise the app falls back to the
local SQLite db.py. The secret key must come from the environment, never code.

Tables (create once via Supabase SQL editor — see backend/supabase_schema.sql):
    kv(key text pk, value jsonb)
    daily_sales(day text, rider_id text, ld int, pk(day,rider_id))
    profiles(id text pk, nickname text, quote text, photo text)
    ingest_log(ts text pk, riders_json jsonb)
"""
import os
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

from . import standings as st
from .models import Config

_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SECRET") or ""
_REST = f"{_URL}/rest/v1"


def _headers(extra: Optional[dict] = None) -> dict:
    h = {
        "apikey": _KEY,
        "Authorization": f"Bearer {_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _client() -> httpx.Client:
    return httpx.Client(timeout=15)


def init_db():
    """No DDL over PostgREST — just verify connectivity (tables made via SQL)."""
    if not _URL or not _KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set for the Supabase backend.")
    with _client() as c:
        r = c.get(f"{_REST}/kv", headers=_headers(), params={"select": "key", "limit": 1})
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase not reachable / tables missing: {r.status_code} {r.text}")


# ---------- kv helpers (value is jsonb) -------------------------------------
def kv_get(key: str, default=None):
    with _client() as c:
        r = c.get(f"{_REST}/kv", headers=_headers(),
                  params={"select": "value", "key": f"eq.{key}"})
    if r.status_code >= 400:
        return default
    rows = r.json()
    return rows[0]["value"] if rows else default


def kv_set(key: str, value):
    with _client() as c:
        c.post(f"{_REST}/kv",
               headers=_headers({"Prefer": "resolution=merge-duplicates"}),
               json=[{"key": key, "value": value}])


# ---------- config -----------------------------------------------------------
def get_config() -> Config:
    raw = kv_get("config")
    return Config(**raw) if raw else Config()


def save_config(cfg: Config):
    kv_set("config", cfg.model_dump())


# ---------- dataset + movement snapshots ------------------------------------
def get_dataset() -> list[dict]:
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
    with _client() as c:
        r = c.get(f"{_REST}/daily_sales", headers=_headers(),
                  params={"select": "day,rider_id,ld", "order": "day.asc"})
    rows = r.json() if r.status_code < 400 else []
    history: dict[str, list[tuple[str, int]]] = {}
    for row in rows:
        history.setdefault(row["rider_id"], []).append((row["day"], row["ld"]))
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


# ---------- the ingest transaction ------------------------------------------
def record_ingest(riders: list[dict], when: Optional[datetime] = None) -> dict:
    when = when or datetime.now()
    cfg = get_config()

    # 1. snapshot previous board for movement
    old = get_dataset()
    if old:
        old_res = st.compute_standings(old, race_metric=cfg.raceMetric)
        pr, ps = st.snapshot_ranks(old_res["riders"])
        kv_set("prev_rank", pr)
        kv_set("prev_sales", ps)
    else:
        kv_set("prev_rank", {})
        kv_set("prev_sales", {})

    # 2. weekly Monday baseline
    monday = _monday_of(when.date())
    new_week = kv_get("week_start") != monday
    if new_week:
        source = old if old else riders
        kv_set("week_baseline", {a["id"]: int(a.get("ld") or 0) for a in source})
        kv_set("week_start", monday)

    # 3. daily snapshot (upsert today's totals)
    today = when.date().isoformat()
    with _client() as c:
        c.post(f"{_REST}/daily_sales",
               headers=_headers({"Prefer": "resolution=merge-duplicates"}),
               json=[{"day": today, "rider_id": a["id"], "ld": int(a.get("ld") or 0)} for a in riders])

    # 4. store dataset + timestamp + log
    iso = when.isoformat()
    kv_set("dataset", riders)
    kv_set("lastUpdated", iso)
    cfg.lastUpdated = iso
    save_config(cfg)
    with _client() as c:
        c.post(f"{_REST}/ingest_log",
               headers=_headers({"Prefer": "resolution=merge-duplicates"}),
               json=[{"ts": iso, "riders_json": riders}])

    # 5. every rider gets a racer bib number (new agents get the next free one)
    bibs = kv_get("bibs", {}) or {}
    new_bibs = st.assign_missing_bibs(bibs, riders, cfg.separateTeams or [])
    if new_bibs != bibs:
        kv_set("bibs", new_bibs)

    return {"lastUpdated": iso, "newWeek": new_week}


# ---------- profiles ---------------------------------------------------------
def get_profile(rider_id: str) -> dict:
    with _client() as c:
        r = c.get(f"{_REST}/profiles", headers=_headers(),
                  params={"select": "nickname,quote,photo", "id": f"eq.{rider_id}"})
    rows = r.json() if r.status_code < 400 else []
    return rows[0] if rows else {"nickname": "", "quote": "", "photo": ""}


def save_profile(rider_id: str, data: dict):
    with _client() as c:
        c.post(f"{_REST}/profiles",
               headers=_headers({"Prefer": "resolution=merge-duplicates"}),
               json=[{"id": rider_id, "nickname": data.get("nickname", ""),
                      "quote": data.get("quote", ""), "photo": data.get("photo", "")}])


def all_profiles() -> dict:
    with _client() as c:
        r = c.get(f"{_REST}/profiles", headers=_headers(),
                  params={"select": "id,nickname,quote,photo"})
    rows = r.json() if r.status_code < 400 else []
    return {row["id"]: {"nickname": row["nickname"], "quote": row["quote"], "photo": row["photo"]} for row in rows}
