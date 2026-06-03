"""Media uploads (handoff §9: prefer an uploaded URL over a data-URL).

Stores rider photos and moment videos in Supabase Storage and returns a public
URL, so they're shared across devices instead of living in one browser. When
Supabase isn't configured (local dev), it falls back to a local uploads folder
served at /uploads/...
"""
import os
import re
import time
import httpx

_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SECRET") or ""
BUCKET = os.environ.get("TOB_BUCKET", "tob-media")
LOCAL_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "uploads")


def _supabase() -> bool:
    return bool(_URL and _KEY)


def _headers(extra=None):
    h = {"apikey": _KEY, "Authorization": f"Bearer {_KEY}"}
    if extra:
        h.update(extra)
    return h


def _safe(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "file")
    return f"{int(time.time() * 1000)}-{name}"


def ensure_bucket():
    """Create the public media bucket once (no-op if it exists / local mode)."""
    if not _supabase():
        os.makedirs(LOCAL_DIR, exist_ok=True)
        return
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{_URL}/storage/v1/bucket/{BUCKET}", headers=_headers())
        if r.status_code == 200:
            return
        c.post(f"{_URL}/storage/v1/bucket", headers=_headers({"Content-Type": "application/json"}),
               json={"id": BUCKET, "name": BUCKET, "public": True,
                     "fileSizeLimit": 52428800})  # 50 MB


def upload(folder: str, filename: str, data: bytes, content_type: str) -> str:
    """Store bytes and return a public URL."""
    path = f"{folder}/{_safe(filename)}"
    if not _supabase():
        os.makedirs(LOCAL_DIR, exist_ok=True)
        local_name = path.replace("/", "__")
        with open(os.path.join(LOCAL_DIR, local_name), "wb") as f:
            f.write(data)
        return f"/uploads/{local_name}"
    with httpx.Client(timeout=60) as c:
        r = c.post(
            f"{_URL}/storage/v1/object/{BUCKET}/{path}",
            headers=_headers({"Content-Type": content_type or "application/octet-stream",
                              "x-upsert": "true"}),
            content=data,
        )
        r.raise_for_status()
    return f"{_URL}/storage/v1/object/public/{BUCKET}/{path}"
