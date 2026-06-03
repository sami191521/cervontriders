"""Vercel serverless entrypoint — exposes the FastAPI ASGI app.

Vercel's @vercel/python runtime serves the module-level `app` object. Supabase
holds all state, so running as stateless serverless functions is fine. (Direct
video-file uploads are limited by Vercel's ~4.5 MB request size; use video URLs.)
"""
import os
import sys

# make the repo-root packages (app/) importable from this nested file
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402  — Vercel serves this ASGI `app`
