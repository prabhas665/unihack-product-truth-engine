from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    batch,
    dashboard,
    downloads,
    enrich,
    evaluation,
    health,
    lookup,
)
from app.unihack.paths import repo_root
from app.config import settings
from app.db.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

# CORS is driven by settings so a public deployment can set the browser origin
# without code changes. The frontend uses no credentials, so credentials stay
# off; methods/headers are open for the JSON POST endpoints (preflight safe).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(enrich.router)
app.include_router(lookup.router)
app.include_router(dashboard.router)
app.include_router(batch.router)
app.include_router(downloads.router)
app.include_router(evaluation.router)


# Optional same-origin frontend serving (single-service deployment). The built
# dist is mounted at "/" after the API routers so /api/* still wins; the SPA
# has no client-side routing, so the index catch-all is sufficient.
# When FRONTEND_DIST_DIR is empty the built SPA at repo_root()/frontend/dist is
# served automatically if it exists (single-service deployments on Render).
_dist = settings.frontend_dist_dir.strip()
if not _dist:
    _default_dist = repo_root() / "frontend" / "dist"
    if _default_dist.is_dir():
        _dist = str(_default_dist)
if _dist:
    _dist_path = Path(_dist)
    if _dist_path.is_dir():
        app.mount("/", StaticFiles(directory=str(_dist_path), html=True), name="static")
