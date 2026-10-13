"""Vercel Python function: the hosted Brink API (all of /api/*, see vercel.json rewrites).

The engine and the service live in src/brink (bundled with this function via `includeFiles`); this file
only puts them on the import path and exposes the ASGI app.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brink.hosted.app import app  # noqa: E402,F401
