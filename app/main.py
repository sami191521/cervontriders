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

from . import constants, ingest as ig, standings as st, auth, media
# storage backend: Supabase when configured, else local SQLite (dev)
if os.environ.get("SUPABASE_URL"):
    from . import db_supabase as db
else:
    from . import db
from .models import (
    Config, ConfigUpdate, CredentialUpdate, Profile, StandingsResponse,
    IngestSummary, RaceMetric,
)

# paths for serving the wired reference UI + adapter
_HERE = os.path.dirname(__file__)
_STATIC_DIR = os.path.join(_HERE, "..", "static")
_HTML_PATH = os.path.join(_HERE, "..", "static", "caye-talkers-tour-of-belize.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:
        media.ensure_bucket()
    except Exception as e:  # storage is optional; don't block startup
        print("[media] bucket setup skipped:", e)
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
    # weekly stage standings: riders ranked by this week's score (§8). Empty
    # until a Monday baseline exists, which tells the UI to fall back to GC.
    stage = sorted([r for r in riders if r.week > 0],
                   key=lambda r: (-r.week, -r.r, r.n))
    return StandingsResponse(
        lastUpdated=cfg.lastUpdated, raceMetric=metric, scope=scope,
        riders=riders, teams=res["teams"], jerseys=res["jerseys"],
        stageWinners=stage, weekStart=db.kv_get("week_start"),
    )


# ---------- meta -------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "tour-of-belize", "version": app.version}


@app.get("/api/route")
def route():
    return {"route": constants.ROUTE, "miles": constants.MILES}


# ---------- auth -------------------------------------------------------------
def _current_admin_user() -> str:
    creds = db.kv_get("admin_creds")
    return creds["user"] if creds else auth.ADMIN_USER


def _verify_login(user: str, password: str) -> bool:
    """DB-stored credentials if the admin has set them, else env bootstrap."""
    creds = db.kv_get("admin_creds")
    if creds:
        return auth.verify_stored(user, password, creds)
    return auth.check_credentials(user, password)


@app.post("/api/auth/login")
def login(body: dict = Body(...)):
    if not _verify_login(body.get("user", ""), body.get("pass", "")):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    return {"token": auth.make_token(body.get("user", "admin"))}


@app.put("/api/admin/credentials")
def change_credentials(body: CredentialUpdate, _: bool = Depends(auth.require_admin)):
    """Admin changes their own username and/or password (requires current password)."""
    # verify against the typed current username + password (falls back to the
    # stored username if the client didn't send one)
    cur_user = (body.currentUser or "").strip() or _current_admin_user()
    if not _verify_login(cur_user, body.currentPass):
        raise HTTPException(status_code=403, detail="Current username or password is incorrect.")
    if not body.newUser and not body.newPass:
        raise HTTPException(status_code=400, detail="Provide newUser and/or newPass.")
    if body.newPass is not None and len(body.newPass) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")
    if body.newUser is not None and not body.newUser.strip():
        raise HTTPException(status_code=400, detail="New username cannot be empty.")

    creds = db.kv_get("admin_creds")
    new_user = (body.newUser or cur_user).strip()
    if body.newPass:
        new_hash = auth.hash_password(body.newPass)
    elif creds:
        new_hash = creds["hash"]                 # username-only change
    else:
        new_hash = auth.hash_password(body.currentPass)  # migrate env creds into DB
    db.kv_set("admin_creds", {"user": new_user, "hash": new_hash})
    # re-issue a token so the client stays logged in under the (possibly) new name
    return {"ok": True, "user": new_user, "token": auth.make_token(new_user)}


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


# ---------- media uploads (handoff §9) --------------------------------------
@app.post("/api/upload/photo")
async def upload_photo(file: UploadFile = File(...), _: bool = Depends(auth.require_admin)):
    """Upload a rider photo, return its shareable URL (admin — part of profiles)."""
    data = await file.read()
    if len(data) > 5_000_000:
        raise HTTPException(status_code=413, detail="Photo too large (max 5 MB).")
    url = media.upload("photos", file.filename or "photo.jpg", data,
                       file.content_type or "image/jpeg")
    return {"url": url}


@app.post("/api/upload/video")
async def upload_video(file: UploadFile = File(...), _: bool = Depends(auth.require_admin)):
    """Upload a moment video (admin), return its shareable URL."""
    data = await file.read()
    if len(data) > 50_000_000:
        raise HTTPException(status_code=413, detail="Video too large (max 50 MB).")
    url = media.upload("videos", file.filename or "video.mp4", data,
                       file.content_type or "video/mp4")
    return {"url": url}


# ---------- profiles ---------------------------------------------------------
@app.get("/api/profiles")
def get_profiles():
    """All rider profiles at once (the board loads these in one shot)."""
    return db.all_profiles()


@app.get("/api/riders/{rider_id}/profile", response_model=Profile)
def get_profile(rider_id: str):
    return Profile(**db.get_profile(rider_id))


@app.put("/api/riders/{rider_id}/profile", response_model=Profile)
def put_profile(rider_id: str, profile: Profile, _: bool = Depends(auth.require_admin)):
    """Set a rider's nickname/quote/photo (admin only — shared across devices)."""
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


@app.get("/uploads/{name}")
def uploaded(name: str):
    """Serve locally-stored uploads (used only when Supabase Storage is off)."""
    return FileResponse(os.path.join(_STATIC_DIR, "uploads", os.path.basename(name)))
