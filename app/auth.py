"""Server-side admin auth (replaces the admin/admin front-end curtain, §intro).

A stateless HMAC-signed bearer token: no session store, survives restarts as
long as TOB_SECRET is stable. Credentials come from env, not client code.

Production: front this with HTTPS, set TOB_SECRET + strong TOB_ADMIN_PASS,
and consider rotating tokens / shorter TTLs.
"""
import os
import time
import json
import hmac
import base64
import hashlib
import secrets

from fastapi import Header, HTTPException

ADMIN_USER = os.environ.get("TOB_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("TOB_ADMIN_PASS", "admin")
# stable secret across restarts if set; otherwise ephemeral (tokens die on restart)
_SECRET = (os.environ.get("TOB_SECRET") or secrets.token_hex(32)).encode()
_TTL = int(os.environ.get("TOB_TOKEN_TTL", "43200"))  # seconds (default 12h)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    return _b64(hmac.new(_SECRET, payload_b64.encode(), hashlib.sha256).digest())


def make_token(user: str) -> str:
    payload = _b64(json.dumps({"u": user, "exp": int(time.time()) + _TTL}).encode())
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> bool:
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return False
    return int(data.get("exp", 0)) > time.time()


def check_credentials(user: str, password: str) -> bool:
    """Env bootstrap credentials (used until the admin sets their own)."""
    return (
        hmac.compare_digest(user or "", ADMIN_USER)
        and hmac.compare_digest(password or "", ADMIN_PASS)
    )


# ---- stored (DB) credentials: salted PBKDF2 hash, never plaintext ----------
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    """Return 'salt$hash' for storage."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", (password or "").encode(),
                            bytes.fromhex(salt), _PBKDF2_ROUNDS).hex()
    return f"{salt}${h}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = (stored or "").split("$", 1)
        calc = hashlib.pbkdf2_hmac("sha256", (password or "").encode(),
                                   bytes.fromhex(salt), _PBKDF2_ROUNDS).hex()
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(calc, h)


def verify_stored(user: str, password: str, creds: dict) -> bool:
    """Check a login against DB-stored credentials {user, hash}."""
    return (
        hmac.compare_digest(user or "", creds.get("user", ""))
        and verify_password(password, creds.get("hash", ""))
    )


def require_admin(authorization: str = Header(default="")):
    """FastAPI dependency: 401 unless a valid Bearer token is present."""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Admin authentication required.")
    return True
