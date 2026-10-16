"""Production entrypoint: the API from app.main plus the static Next.js export on the same origin.

    uvicorn app.serve:app --host 0.0.0.0 --port $PORT

FRONTEND_DIR points at the `next build` static export (frontend/out). API routes are registered first, so
/api/*, /health and /docs always win; everything else falls through to the static files. If the directory
is missing, the API runs alone (useful for local dev, where Next.js serves the UI itself).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from . import config
from .main import app

log = logging.getLogger("patientzero")
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR") or (config.ROOT / "frontend" / "out"))

if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="web")
    log.info("serving static frontend from %s", FRONTEND_DIR)
else:
    log.warning("FRONTEND_DIR %s has no index.html; serving the API only", FRONTEND_DIR)
