"""Application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import router as auth_router
from .config import get_settings
from .db import SessionLocal, engine
from .models import Base
from .routers.api import router as api_router
from .routers.feeds import router as feeds_router
from .seed import seed_if_empty

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("conference-tracker")

settings = get_settings()
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    if settings.seed_on_start:
        with SessionLocal() as db:
            created = seed_if_empty(db, settings.seed_file)
            if created:
                log.info("database seeded with %d conferences", created)
    if not settings.auth_enabled:
        log.warning("OIDC is not configured — running in single-user dev mode")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url="/api/docs")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="ct_session",
    https_only=settings.base_url.startswith("https://"),
    same_site="lax",
)
app.include_router(api_router)
app.include_router(feeds_router)
app.include_router(auth_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
