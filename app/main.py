"""Tour of Belize — live backend (FastAPI).

The hosted "Option B": one shared source of truth for every TV and phone.
Viewers are login-free; ingest + config writes require an admin token.
"""
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import constants, ingest as ig, standings as st, db, auth
from .models import (
    Config, ConfigUpdate, Profile, StandingsResponse, IngestSummary, RaceMetric,
)

# paths for serving the wired reference UI + adapter
_HERE = os.path.dirname(__file__)
_STATIC_DIR = os.path.join(_HERE, "..", "static")
_HTML_PATH = os.path.join(_HERE, "..", "static", "caye-talkers-tour-of-belize.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="Tour of Belize API",
    description="Live, shared scoreboard backend for the Caye Talkers Tour of Belize.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- composition: build the standings response -----------------------
def build_standings(metric: Optional[str] = None, scope: str = "all") -> StandingsResponse:
    cfg = db.get_config()
    metric = metric or cfg.raceMetric
    data = db.get_dataset()
    res = st.compute_standings(
        data,
        race_metric=metric,
        prev_rank=db.get_prev_rank(),
        prev_sales=db.get_prev_sales(),
        week_baseline=db.get_week_baseline() or None,
        streaks=db.get_streaks(),
    )
    riders = res["riders"]
    if scope != "all":
        riders = [r for r in riders if r.tl == scope]   # keep GC ranks within group
    return StandingsResponse(
        lastUpdated=cfg.lastUpdated, raceMetric=metric, scope=scope,
        riders=riders, teams=res["teams"], jerseys=res["jerseys"],
    )


# ---------- meta -------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "tour-of-belize", "version": app.version}


@app.get("/api/route")
def route():
    return {"route": constants.ROUTE, "miles": constants.MILES}


# ---------- auth -------------------------------------------------------------
@app.post("/api/auth/login")
def login(body: dict = Body(...)):
    if not auth.check_credentials(body.get("user", ""), body.get("pass", "")):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    return {"token": auth.make_token(body.get("user", "admin"))}


# ---------- ingest -----------------------------------------------------------
@app.post("/api/ingest/preview")
async def ingest_preview(file: UploadFile = File(...), _: bool = Depends(auth.require_admin)):
    """Parse without persisting: return headers, auto-mapping, and a sample."""
    headers, rows = ig.parse_upload(file.filename or "upload.csv", await file.read())
    if not headers or not rows:
        raise HTTPException(status_code=400, detail="Could not find a header row and data.")
    return {
        "headers": headers,
        "rowCount": len(rows),
        "mapping": ig.auto_map(headers),
        "fields": [{"key": k, "label": lbl, "required": req} for (k, lbl, req, _al) in ig.CANON],
        "sample": rows[:3],
    }


@app.post("/api/ingest/json", response_model=IngestSummary)
def ingest_json(body: dict = Body(...), _: bool = Depends(auth.require_admin)):
    """Persist already-normalized riders (the wired front-end posts these).

    Server still owns movement / weekly baseline / streak via record_ingest.
    """
    riders = body.get("riders") or []
    if not isinstance(riders, list) or not riders:
        raise HTTPException(status_code=400, detail="Body must include a non-empty 'riders' list.")
    for a in riders:
        if not a.get("id") and a.get("n"):
            a["id"] = ig.slugify(a["n"])
    info = db.record_ingest(riders)
    return IngestSummary(
        riders=len(riders),
        teams=len({(a.get("tl") or "Unassigned") for a in riders}),
        lastUpdated=info["lastUpdated"],
        newWeek=info["newWeek"],
    )


@app.post("/api/ingest", response_model=IngestSummary)
async def ingest(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    _: bool = Depends(auth.require_admin),
):
    """Upload + normalize + persist + recompute (admin). Optional mapping override (JSON)."""
    import json
    headers, rows = ig.parse_upload(file.filename or "upload.csv", await file.read())
    if not headers or not rows:
        raise HTTPException(status_code=400, detail="Could not find a header row and data.")
    field_map = ig.auto_map(headers)
    if mapping:
        try:
            field_map.update({k: v for k, v in json.loads(mapping).items() if v})
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="mapping must be valid JSON.")
    try:
        riders = ig.build_riders(rows, field_map)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    info = db.record_ingest(riders)
    return IngestSummary(
        riders=len(riders),
        teams=len({r["tl"] for r in riders}),
        lastUpdated=info["lastUpdated"],
        newWeek=info["newWeek"],
    )


# ---------- standings --------------------------------------------------------
@app.get("/api/standings", response_model=StandingsResponse)
def standings(metric: Optional[RaceMetric] = None, scope: str = "all"):
    return build_standings(metric, scope)


# ---------- config -----------------------------------------------------------
@app.get("/api/config", response_model=Config)
def get_config():
    return db.get_config()


@app.put("/api/config", response_model=Config)
def put_config(update: ConfigUpdate, _: bool = Depends(auth.require_admin)):
    cfg = db.get_config()
    merged = cfg.model_dump()
    merged.update({k: v for k, v in update.model_dump(exclude_unset=True).items() if v is not None})
    new_cfg = Config(**merged)
    db.save_config(new_cfg)
    return new_cfg


# ---------- profiles ---------------------------------------------------------
@app.get("/api/profiles")
def get_profiles():
    """All rider profiles at once (the board loads these in one shot)."""
    return db.all_profiles()


@app.get("/api/riders/{rider_id}/profile", response_model=Profile)
def get_profile(rider_id: str):
    return Profile(**db.get_profile(rider_id))


@app.put("/api/riders/{rider_id}/profile", response_model=Profile)
def put_profile(rider_id: str, profile: Profile):
    db.save_profile(rider_id, profile.model_dump())
    return profile


# ---------- serve the wired UI ----------------------------------------------
@app.get("/")
def index():
    return FileResponse(_HTML_PATH)


@app.get("/api-adapter.js")
def adapter_js():
    return FileResponse(os.path.join(_STATIC_DIR, "api-adapter.js"),
                        media_type="application/javascript")
