"""Application entrypoint."""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import router as auth_router
from .config import get_settings
from .db import SessionLocal, engine
from .editing import EditError
from .mcp_server import gate as mcp_gate
from .mcp_server import running as mcp_running
from .migrate import migrate
from .routers.api import router as api_router
from .routers.feeds import router as feeds_router
from .seed import seed_if_empty
from .version import VERSION, footer_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("conference-tracker")

settings = get_settings()
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.init_db_on_start:
        migrate(engine)
        if settings.seed_on_start:
            with SessionLocal() as db:
                created = seed_if_empty(db, settings.seed_file)
                if created:
                    log.info("database seeded with %d conferences", created)
    else:
        log.info("INIT_DB_ON_START=false — expecting the migration job to have run")
    if not settings.auth_enabled:
        log.warning("OIDC is not configured — running in single-user dev mode")
    async with mcp_running():
        yield


app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url="/api/docs")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="ct_session",
    https_only=settings.base_url.startswith("https://"),
    same_site="lax",
)


@app.exception_handler(EditError)
async def edit_error(_: Request, exc: EditError) -> JSONResponse:
    """Refusals from app/editing.py (no such record, a clash, a stale undo)."""
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


app.include_router(api_router)
app.include_router(feeds_router)
app.include_router(auth_router)
# The MCP server (app/mcp_server.py): admin API tokens only, checked before the SDK sees a byte.
app.add_route("/mcp", mcp_gate, include_in_schema=False)


class _Static(StaticFiles):
    """The page links its script and stylesheet as ?v=<content hash>, so such a URL
    never changes meaning and can be kept for good; anything else is rechecked."""

    async def get_response(self, path, scope):
        res = await super().get_response(path, scope)
        if res.status_code in (200, 304):
            pinned = b"v=" in scope.get("query_string", b"")
            res.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if pinned else "no-cache"
            )
        return res


app.mount("/static", _Static(directory=STATIC_DIR), name="static")
ASSETS = ("app.js", "styles.css")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"status": "ok", "version": VERSION}


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    """The page, with its assets pinned to their content so a release is picked up on
    the next load (browsers otherwise kept serving the previous app.js for days)."""
    page = (STATIC_DIR / "index.html").read_text()
    for name in ASSETS:
        tag = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12]
        page = page.replace(f'"/static/{name}"', f'"/static/{name}?v={tag}"')
    page = page.replace("<!--version-->", footer_html())
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})
