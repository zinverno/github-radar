"""Route aggregation for the web API.

Each module below exposes a single ``router``; this package collapses them
into one ``api_router`` the application mounts under ``/api/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter

from github_radar.api.routes.ai import router as ai_router
from github_radar.api.routes.bridges import router as bridges_router
from github_radar.api.routes.developers import router as developers_router
from github_radar.api.routes.ecosystem import router as ecosystem_router
from github_radar.api.routes.repositories import router as repositories_router
from github_radar.api.routes.search import router as search_router
from github_radar.api.routes.status import router as status_router
from github_radar.api.routes.topics import router as topics_router
from github_radar.api.routes.workspace import router as workspace_router

api_router = APIRouter()
api_router.include_router(status_router)
api_router.include_router(repositories_router)
api_router.include_router(topics_router)
api_router.include_router(developers_router)
api_router.include_router(ecosystem_router)
api_router.include_router(bridges_router)
api_router.include_router(search_router)
api_router.include_router(ai_router)
api_router.include_router(workspace_router)

__all__ = ["api_router"]
