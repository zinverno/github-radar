"""HTTP API (Phase 6): a read-mostly JSON surface over the analytics layers.

The API never reimplements analytics; it projects the deterministic reports of
Phases 1–5 into JSON. Writes are limited to explicit AI artifact generation
(``POST`` only — a ``GET`` never triggers a model call).
"""

from __future__ import annotations

from github_radar.api.app import create_app

__all__ = ["create_app"]