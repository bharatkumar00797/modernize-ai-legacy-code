"""Serverless entrypoint (Vercel): exposes the Modernize AI FastAPI app as ``app``.

Serverless functions freeze or discard background threads once a response is sent, so runs
finish synchronously inside ``POST /api/runs`` (``MODERNIZE_SYNC_RUNS`` is switched on
automatically when ``VERCEL`` is set, and set here as a fallback). Unless API keys are configured
the deployment is a public demo: packaged examples and the offline mock provider.

Runs live in memory per function instance, so the full report and events are returned in
the POST response itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("MODERNIZE_SYNC_RUNS", "true")
os.environ.setdefault("MODERNIZE_MAX_CONCURRENT_RUNS", "1")

from modernize.api import create_app  # noqa: E402  (path setup must run first)

app = create_app()
