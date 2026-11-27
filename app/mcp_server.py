"""An MCP server at /mcp, so an AI can keep the tracker up to date.

It speaks Streamable HTTP (stateless, JSON responses) and requires an admin's
API token, `Authorization: Bearer ct_…`, on every request; nothing else gets
in, not a browser session and not dev mode. Tools act as the token's owner and
every write goes through app/editing.py, so it lands in the change log with the
token's name and can be undone like any other edit.

Rules the tools enforce, beyond what the REST API does:
- every add or update cites `source_url`, the page the new values were read from;
- updates are partial: only the fields given change, so the AI never writes
  back stale copies of fields it didn't mean to touch;
- no tool deletes a series or an edition; deleting a milestone needs a reason.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, TypeVar

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from . import editing
from .auth import _bearer, _token_user
from .db import SessionLocal
from .editing import Actor, EditError
from .editions import aoe_today
from .models import ApiToken, Change, Conference, Series
from .routers.api import _change_out, _find_series, _series_detail, search_series
from .schemas import ChangeOut, EditionIn, MilestoneIn, SeriesDetail, SeriesIn, SeriesOut
from .version import VERSION

INSTRUCTIONS = """\
Conference Tracker: submission deadlines for the conferences a research group targets.

Model
- A *series* is a recurring conference (ESWC). An *edition* is one year of it (ESWC 2027),
  with key dates (abstract, paper, notification, camera-ready, start, end) and a *milestone*
  timeline (each dated step, per track). Searching a series gives the edition to show: the
  earliest one with a submission deadline still ahead, else the latest.
- A series' `state` says what its shown edition lacks: "open" (a deadline ahead), "closed"
  (deadlines passed; next edition not added yet), "awaiting" (no deadline known yet).
  "closed" and "awaiting" series are the to-do list.

Conventions
- Dates are YYYY-MM-DD. Deadlines are 23:59 AoE (UTC-12) unless the CFP says otherwise;
  record the CFP's own basis in a milestone's `time_basis`, e.g. "23:59 AoE (UTC−12)",
  "Event local dates", "Not specified".
- Milestone names in use: "Abstract registration", "Full paper submission", "Notification",
  "Camera-ready", "Conference window"; tracks like "Main track", "Workshop proposals",
  "PhD Symposium", "Event". Reuse these rather than inventing variants.
- Set an edition's key dates and add milestones for its full timeline; keep them consistent.

Rules
- Read before writing: tracker_get_series shows every edition, milestone and id.
- Cite the official page you read in `source_url` on every add/update. Use official
  sites and CFPs, not aggregators, when you can.
- Updates are partial: pass only the fields that change.
- Every write is logged and can be undone with tracker_undo_change; report the change ids.
- You cannot delete series or editions; ask a person.
"""

mcp = MCPServer("conference_tracker_mcp", version=VERSION, instructions=INSTRUCTIONS)


def tool(annotations: ToolAnnotations):
    """@mcp.tool, with the docstring dedented so the model doesn't read the indentation."""

    def register(fn):
        return mcp.tool(annotations=annotations, description=inspect.cleandoc(fn.__doc__))(fn)

    return register


# --------------------------------------------------------------------------
# Plumbing: the token gate, the per-loop session manager, and shared helpers.
# --------------------------------------------------------------------------
_TOKEN_KEY = "conference_tracker.token_id"
# One session manager per event loop: its run() can be entered once, and tests
# start the app more than once (each TestClient has its own loop).
_managers: dict[asyncio.AbstractEventLoop, Any] = {}


@asynccontextmanager
async def running() -> AsyncIterator[None]:
    """Run the MCP session manager for as long as the app is up (call from the lifespan)."""
    mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        # The SDK's DNS-rebinding guard protects unauthenticated local servers; this
        # one is public and refuses every request without a valid admin token.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    manager = mcp.session_manager
    loop = asyncio.get_running_loop()
    _managers[loop] = manager
    try:
        async with manager.run():
            yield
    finally:
        _managers.pop(loop, None)


def _admit(raw: str | None) -> tuple[int, str] | str:
    """The token's id, or (status, reason) to refuse with."""
    if raw is None:
        return 401, "Send Authorization: Bearer <API token>; create one under Admin → API tokens"
    with SessionLocal() as db:
        try:
            user, tok = _token_user(db, raw)
        except HTTPException as exc:
            return exc.status_code, str(exc.detail)
        if not user.is_admin:
            return 403, "This token's owner is no longer an administrator"
        return tok.id


class _Gate:
    """ASGI endpoint for /mcp: admit admin tokens only, then hand over to the SDK."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("method") != "POST":
            # Stateless JSON mode needs only POST. GET would open an event stream
            # that outlives the token check (revoking would not close it).
            await JSONResponse({"detail": "Use POST"}, 405, headers={"Allow": "POST"})(
                scope, receive, send
            )
            return
        verdict = await run_in_threadpool(_admit, _bearer(Request(scope)))
        if isinstance(verdict, tuple):
            code, reason = verdict
            headers = {"WWW-Authenticate": "Bearer"} if code == 401 else None
            await JSONResponse({"detail": reason}, code, headers=headers)(scope, receive, send)
            return
        scope[_TOKEN_KEY] = verdict
        manager = _managers.get(asyncio.get_running_loop())
        if manager is None:
            await JSONResponse({"detail": "MCP is starting up"}, 503)(scope, receive, send)
            return
        await manager.asgi_app(scope, receive, send)


gate = _Gate()

T = TypeVar("T")


def _act(ctx: Context, fn: Callable[[Session, Actor], T], source: str = "", note: str = "") -> T:
    """Run fn(db, actor) as the token's owner; turn refusals into errors the model reads."""
    token_id = ctx.request_context.request.scope[_TOKEN_KEY]
    with SessionLocal() as db:
        tok = db.get(ApiToken, token_id)
        try:
            return fn(db, Actor(user=tok.user, token=tok, source=source, note=note))
        except EditError as exc:
            raise ToolError(exc.detail) from exc
        except ValidationError as exc:
            raise ToolError(_explain(exc)) from exc


def _read(fn: Callable[[Session], T]) -> T:
    with SessionLocal() as db:
        return fn(db)


def _explain(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()
    )


def _series(db: Session, key: str) -> Series:
    s = _find_series(db, key.strip())
    if s is None:
        raise ToolError(f"No series {key!r}. tracker_search_series lists them.")
    return s


_LABEL = re.compile(r"\s*(.+?)\s+(\d{4})\s*")


def _edition(db: Session, key: str) -> Conference:
    """By id, or by label: "ESWC 2027"."""
    m = _LABEL.fullmatch(key)
    if m:
        s = _find_series(db, m.group(1))
        if s is not None:
            for e in s.editions:
                if e.year == int(m.group(2)):
                    return e
    conf = db.get(Conference, key.strip())
    if conf is None:
        raise ToolError(
            f"No edition {key!r}. Give its id or a label like 'ESWC 2027'; "
            "tracker_get_series lists a series' editions."
        )
    return conf


def _merge(model: type[BaseModel], current: Any, given: dict) -> Any:
    """The stored record's fields with the given ones on top, validated.

    Only the given fields are held to today's rules: a stored value written
    before a rule existed (a homepage without http://) is kept as it is rather
    than blocking an unrelated change.
    """
    given = {k: v for k, v in given.items() if v is not None}
    values = {f: getattr(current, f) for f in model.model_fields} | given
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        failed = {e["loc"][0] for e in exc.errors() if e["loc"]}
        if not failed or failed & given.keys():
            raise
        data = model.model_validate({k: v for k, v in values.items() if k not in failed})
        for k in failed:
            setattr(data, k, values[k])
        return data


class WriteResult(BaseModel):
    changed: bool = Field(description="False when the record already had these values")
    id: str = Field(description="The record's id")
    change: ChangeOut | None = Field(
        None, description="The change-log entry; undo it with tracker_undo_change(change.id)"
    )


def _result(actor: Actor, record_id: str) -> WriteResult:
    """What this call wrote: the change its write recorded, if anything changed."""
    change = actor.recorded[-1] if actor.recorded else None
    return WriteResult(
        changed=change is not None,
        id=record_id,
        change=_change_out(change) if change else None,
    )


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------
SourceUrl = Annotated[
    str,
    Field(
        pattern=r"^https?://\S+$",
        max_length=1000,
        description="The page the new values come from (official site or CFP), http(s)",
    ),
]
Note = Annotated[str, Field(max_length=2000, description="Why, for the change log")]
SeriesKey = Annotated[str, Field(description="Series acronym (e.g. 'ESWC') or id")]
EditionKey = Annotated[
    str, Field(description="Edition id, or its label: series acronym and year, e.g. 'ESWC 2027'")
]
Day = Annotated[dt.date | None, Field(description="YYYY-MM-DD")]
DateField = Literal[
    "abstract_date", "paper_date", "notify_date", "camera_date", "start_date", "end_date"
]
Text = str | None

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
ADD = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
EDIT = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
REMOVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
UNDO = REMOVE  # not idempotent: undoing twice is undo, then refused


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
@tool(READ)
def tracker_search_series(
    query: Annotated[str, Field(description="Part of an acronym or name; empty for all")] = "",
    state: Annotated[
        Literal["open", "closed", "awaiting"] | None,
        Field(description="Only series in this state; 'closed' and 'awaiting' need updating"),
    ] = None,
) -> list[SeriesOut]:
    """Find conference series. Each shows its current edition (current_year), whether it is
    open for submissions, and its state. Exact acronym matches come first."""
    found = _read(lambda db: search_series(query, db))
    return [s for s in found if state is None or s.state == state]


@tool(READ)
def tracker_get_series(series: SeriesKey) -> SeriesDetail:
    """One series with every edition, its key dates and its milestone timeline, with ids."""
    return _read(lambda db: _series_detail(_series(db, series), aoe_today()))


@tool(READ)
def tracker_list_changes(
    series: Annotated[str, Field(description="Only changes to this series (acronym or id)")] = "",
    record_id: Annotated[str, Field(description="Only changes to this record")] = "",
    before: Annotated[int | None, Field(description="Page: changes older than this id")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> list[ChangeOut]:
    """The change log, newest first: who changed what, from which source, and whether it
    was undone."""

    def run(db: Session) -> list[ChangeOut]:
        q = select(Change).order_by(Change.id.desc()).limit(limit)
        if series:
            q = q.where(Change.series_id == _series(db, series).id)
        if record_id:
            q = q.where(Change.entity_id == record_id)
        if before is not None:
            q = q.where(Change.id < before)
        return [_change_out(c) for c in db.scalars(q)]

    return _read(run)


# --------------------------------------------------------------------------
# Series
# --------------------------------------------------------------------------
@tool(ADD)
def tracker_add_series(
    ctx: Context,
    acronym: Annotated[str, Field(description="Short name as the community writes it")],
    source_url: SourceUrl,
    name: Annotated[str, Field(description="Full name")] = "",
    homepage: Annotated[str, Field(description="The series' lasting home page, http(s)")] = "",
    emphasis: Annotated[str, Field(description="What the venue is about, one sentence")] = "",
    fit: Annotated[str, Field(description="What work of ours would fit it, one sentence")] = "",
    notes: str = "",
    note: Note = "",
) -> WriteResult:
    """Add a new conference series (a venue not tracked yet). Then add its edition with
    tracker_add_edition."""

    def run(db: Session, actor: Actor) -> WriteResult:
        data = SeriesIn(
            acronym=acronym, name=name, homepage=homepage, emphasis=emphasis, fit=fit, notes=notes
        )
        return _result(actor, editing.create_series(db, actor, data).id)

    return _act(ctx, run, source_url, note)


@tool(EDIT)
def tracker_update_series(
    ctx: Context,
    series: SeriesKey,
    source_url: SourceUrl,
    acronym: Text = None,
    name: Text = None,
    homepage: Text = None,
    emphasis: Text = None,
    fit: Text = None,
    notes: Text = None,
    note: Note = "",
) -> WriteResult:
    """Change a series' lasting facts. Only the fields given change; "" clears one."""
    given = dict(
        acronym=acronym, name=name, homepage=homepage, emphasis=emphasis, fit=fit, notes=notes
    )

    def run(db: Session, actor: Actor) -> WriteResult:
        s = _series(db, series)
        editing.update_series(db, actor, s.id, _merge(SeriesIn, s, given))
        return _result(actor, s.id)

    return _act(ctx, run, source_url, note)


# --------------------------------------------------------------------------
# Editions
# --------------------------------------------------------------------------
@tool(ADD)
def tracker_add_edition(
    ctx: Context,
    series: SeriesKey,
    year: Annotated[int, Field(ge=1950, le=2100)],
    source_url: SourceUrl,
    location: str = "",
    url: Annotated[str, Field(description="This edition's own site, http(s)")] = "",
    status: Annotated[
        str, Field(description="How settled the dates are, e.g. 'Published', 'CFP pending'")
    ] = "",
    notes: str = "",
    abstract_date: Day = None,
    paper_date: Day = None,
    notify_date: Day = None,
    camera_date: Day = None,
    start_date: Day = None,
    end_date: Day = None,
    note: Note = "",
) -> WriteResult:
    """Add a year of a series (e.g. ESWC 2028 once its CFP is out), with its key dates.
    Add its timeline with tracker_add_milestone."""
    fields = dict(
        year=year, location=location, url=url, status=status, notes=notes,
        abstract_date=abstract_date, paper_date=paper_date, notify_date=notify_date,
        camera_date=camera_date, start_date=start_date, end_date=end_date,
    )  # fmt: skip

    def run(db: Session, actor: Actor) -> WriteResult:
        s = _series(db, series)
        return _result(actor, editing.create_edition(db, actor, s.id, EditionIn(**fields)).id)

    return _act(ctx, run, source_url, note)


@tool(EDIT)
def tracker_update_edition(
    ctx: Context,
    edition: EditionKey,
    source_url: SourceUrl,
    year: Annotated[int | None, Field(ge=1950, le=2100)] = None,
    location: Text = None,
    url: Text = None,
    status: Text = None,
    notes: Text = None,
    abstract_date: Day = None,
    paper_date: Day = None,
    notify_date: Day = None,
    camera_date: Day = None,
    start_date: Day = None,
    end_date: Day = None,
    clear: Annotated[
        list[DateField] | None,
        Field(description="Dates to remove, e.g. a deadline that was withdrawn"),
    ] = None,
    note: Note = "",
) -> WriteResult:
    """Change an edition's key dates or details. Only the fields given change."""
    given = dict(
        year=year, location=location, url=url, status=status, notes=notes,
        abstract_date=abstract_date, paper_date=paper_date, notify_date=notify_date,
        camera_date=camera_date, start_date=start_date, end_date=end_date,
    )  # fmt: skip

    def run(db: Session, actor: Actor) -> WriteResult:
        conf = _edition(db, edition)
        data = _merge(EditionIn, conf, given)
        for name in clear or ():
            setattr(data, name, None)
        editing.update_edition(db, actor, conf.id, data)
        return _result(actor, conf.id)

    return _act(ctx, run, source_url, note)


# --------------------------------------------------------------------------
# Milestones
# --------------------------------------------------------------------------
@tool(ADD)
def tracker_add_milestone(
    ctx: Context,
    edition: EditionKey,
    date: Annotated[dt.date, Field(description="YYYY-MM-DD; the first day of a window")],
    milestone: Annotated[str, Field(description="What happens, e.g. 'Full paper submission'")],
    source_url: SourceUrl,
    track: Annotated[str, Field(description="e.g. 'Main track', 'Workshop proposals'")] = "",
    end_date: Day = None,
    time_basis: Annotated[str, Field(description="e.g. '23:59 AoE (UTC−12)'")] = "",
    status: Annotated[str, Field(description="e.g. 'Published', 'Organiser CFP'")] = "",
    notes: str = "",
    note: Note = "",
) -> WriteResult:
    """Add one dated step to an edition's timeline."""

    def run(db: Session, actor: Actor) -> WriteResult:
        conf = _edition(db, edition)
        data = MilestoneIn(
            date=date, end_date=end_date, milestone=milestone, track=track,
            time_basis=time_basis, status=status, notes=notes,
        )  # fmt: skip
        return _result(actor, editing.create_milestone(db, actor, conf.id, data).id)

    return _act(ctx, run, source_url, note)


@tool(EDIT)
def tracker_update_milestone(
    ctx: Context,
    milestone_id: str,
    source_url: SourceUrl,
    date: Day = None,
    end_date: Day = None,
    clear_end_date: bool = False,
    milestone: Text = None,
    track: Text = None,
    time_basis: Text = None,
    status: Text = None,
    notes: Text = None,
    note: Note = "",
) -> WriteResult:
    """Change one milestone, e.g. a deadline that was extended. Only the fields given change."""
    given = dict(
        date=date, end_date=end_date, milestone=milestone, track=track,
        time_basis=time_basis, status=status, notes=notes,
    )  # fmt: skip

    def run(db: Session, actor: Actor) -> WriteResult:
        ms = editing._get(db, "milestone", milestone_id)
        data = _merge(MilestoneIn, ms, given)
        if clear_end_date:
            data.end_date = None
        editing.update_milestone(db, actor, ms.id, data)
        return _result(actor, ms.id)

    return _act(ctx, run, source_url, note)


@tool(REMOVE)
def tracker_delete_milestone(
    ctx: Context,
    milestone_id: str,
    reason: Annotated[str, Field(min_length=3, max_length=2000, description="Why it goes")],
    source_url: Annotated[
        str,
        Field(pattern=r"^(https?://\S+)?$", max_length=1000, description="Supporting page, if any"),
    ] = "",
) -> WriteResult:
    """Remove one milestone that is wrong or duplicated. Prefer tracker_update_milestone
    when a date merely moved."""

    def run(db: Session, actor: Actor) -> WriteResult:
        editing.delete_milestone(db, actor, milestone_id)
        return _result(actor, milestone_id)

    return _act(ctx, run, source_url, reason)


# --------------------------------------------------------------------------
# Undo
# --------------------------------------------------------------------------
@tool(UNDO)
def tracker_undo_change(ctx: Context, change_id: int, note: Note = "") -> WriteResult:
    """Revert one logged change. Refused if the record changed since: undo newer changes
    first. Undoing an undo redoes it. Undoing an addition deletes it, so it needs a `note`
    saying why, and is refused for a series or an edition."""

    def run(db: Session, actor: Actor) -> WriteResult:
        change = db.get(Change, change_id)
        # Undoing a create deletes the record: hold that to the rules on deleting.
        if change is not None and change.action == "create" and change.undone_by is None:
            if change.entity in ("series", "edition"):
                raise ToolError(
                    f"Undoing change {change_id} would delete {change.label}; only a person "
                    "can delete a series or an edition (Admin → Recent changes)."
                )
            if len(note.strip()) < 3:
                raise ToolError(
                    f"Undoing change {change_id} deletes {change.label}: say why in `note`."
                )
        new = editing.undo(db, actor, change_id)
        return _result(actor, new.entity_id)

    return _act(ctx, run, note=note)


__all__ = ["gate", "mcp", "running"]
