"""Every write to series, editions and milestones, each one recorded in the change log.

The REST endpoints and the MCP server (app/mcp_server.py) both edit through these
functions, so whatever the route, a write is checked the same way and lands in
`changes` with who made it, how, and from what source.

Undo works from the log. A change can be undone while the record still looks
exactly as that change left it; otherwise undo refuses rather than overwrite a
later edit. Undoing produces a change of its own, so undoing that is a redo.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import typing
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ApiToken, Change, Conference, Milestone, Series, Submission, User
from .schemas import EditionIn, MilestoneIn, SeriesIn


class EditError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFound(EditError):
    status_code = 404


class Conflict(EditError):
    status_code = 409


@dataclass
class Actor:
    """Who is writing, how, and on what evidence."""

    user: User
    token: ApiToken | None = None
    source: str = ""  # where the new value was read, e.g. the CFP page
    note: str = ""
    # What this actor's writes recorded, in order: a caller reports its own change
    # from here rather than searching the log, where another writer may have been.
    recorded: list[Change] = dataclasses.field(default_factory=list)

    @property
    def via(self) -> str:
        return "token" if self.token else "web"


# --- snapshots -------------------------------------------------------------------

MODELS: dict[str, type] = {"series": Series, "edition": Conference, "milestone": Milestone}
INPUTS: dict[str, type[BaseModel]] = {
    "series": SeriesIn,
    "edition": EditionIn,
    "milestone": MilestoneIn,
}
PARENT = {"edition": "series_id", "milestone": "conference_id"}
# Carried by a deleted edition's snapshot so undo can bring them back.
DEEP_KEYS = ("milestones", "submission_ids")


def _is_date(annotation) -> bool:
    return dt.date in (typing.get_args(annotation) or (annotation,))


DATE_FIELDS = {
    entity: {f for f, info in model.model_fields.items() if _is_date(info.annotation)}
    for entity, model in INPUTS.items()
}


def _json(value):
    return value.isoformat() if isinstance(value, dt.date) else value


def snapshot(entity: str, obj) -> dict:
    """The record's editable fields (plus id and parent), as JSON-ready values."""
    out = {"id": obj.id}
    if entity in PARENT:
        out[PARENT[entity]] = getattr(obj, PARENT[entity])
    out.update({f: _json(getattr(obj, f)) for f in INPUTS[entity].model_fields})
    return out


def _deep(entity: str, obj) -> dict:
    snap = snapshot(entity, obj)
    if entity == "edition":
        snap["milestones"] = [snapshot("milestone", m) for m in obj.milestones]
        snap["submission_ids"] = [s.id for s in obj.submissions]
    return snap


def _shallow(snap: dict | None) -> dict | None:
    return None if snap is None else {k: v for k, v in snap.items() if k not in DEEP_KEYS}


def _values(entity: str, snap: dict) -> dict:
    """Editable fields from a snapshot, with dates back as dates."""
    dates = DATE_FIELDS[entity]
    out = {}
    for f in INPUTS[entity].model_fields:
        if f in snap:
            v = snap[f]
            out[f] = dt.date.fromisoformat(v) if f in dates and isinstance(v, str) else v
    return out


def changed_fields(before: dict | None, after: dict | None) -> list[str]:
    if before is None or after is None:
        return []
    return [k for k in after if k not in DEEP_KEYS and before.get(k) != after.get(k)]


# --- the log ---------------------------------------------------------------------


def _edition_label(db: Session, conf_id: str) -> tuple[str, str | None]:
    conf = db.get(Conference, conf_id)
    return (conf.label, conf.series_id) if conf else ("?", None)


def _describe(db: Session, entity: str, obj) -> tuple[str, str | None]:
    """(label, series id) for the log, e.g. ("ESWC 2027 · Paper submission (Research)", …)."""
    if entity == "series":
        return obj.acronym, obj.id
    if entity == "edition":
        series = db.get(Series, obj.series_id)
        return f"{series.acronym if series else '?'} {obj.year}", obj.series_id
    edition, series_id = _edition_label(db, obj.conference_id)
    label = f"{edition} · {obj.milestone or 'milestone'}"
    return (f"{label} ({obj.track})" if obj.track else label), series_id


def _record(
    db: Session,
    actor: Actor,
    entity: str,
    action: str,
    obj,
    before: dict | None,
    after: dict | None,
    undo_of: int | None = None,
) -> Change:
    label, series_id = _describe(db, entity, obj)
    change = Change(
        actor_id=actor.user.id,
        actor_name=actor.user.name or actor.user.email,
        token_id=actor.token.id if actor.token else None,
        token_name=actor.token.name if actor.token else "",
        via=actor.via,
        entity=entity,
        entity_id=obj.id,
        series_id=series_id,
        action=action,
        label=label[:600],
        before=before,
        after=after,
        source=actor.source,
        note=actor.note,
        undo_of=undo_of,
    )
    db.add(change)
    db.flush()
    actor.recorded.append(change)
    return change


def _create(db, actor, entity, obj, undo_of=None, after_extra=None) -> Change:
    db.add(obj)
    db.flush()
    after = {**snapshot(entity, obj), **(after_extra or {})}
    return _record(db, actor, entity, "create", obj, None, after, undo_of)


def _update(db, actor, entity, obj, values: dict, undo_of=None) -> Change | None:
    before = snapshot(entity, obj)
    for field, value in values.items():
        setattr(obj, field, value)
    db.flush()
    after = snapshot(entity, obj)
    if after == before:
        return None
    return _record(db, actor, entity, "update", obj, before, after, undo_of)


def _delete(db, actor, entity, obj, undo_of=None) -> Change:
    change = _record(db, actor, entity, "delete", obj, _deep(entity, obj), None, undo_of)
    db.delete(obj)
    db.flush()
    return change


# --- checks ----------------------------------------------------------------------


def acronym_taken(db: Session, acronym: str, except_id: str | None = None) -> bool:
    q = select(Series.id).where(func.lower(Series.acronym) == acronym.lower())
    if except_id:
        q = q.where(Series.id != except_id)
    return db.scalar(q) is not None


def year_taken(db: Session, series_id: str, year: int, except_id: str | None = None) -> bool:
    q = select(Conference.id).where(Conference.series_id == series_id, Conference.year == year)
    if except_id:
        q = q.where(Conference.id != except_id)
    return db.scalar(q) is not None


def _check_acronym(db, acronym, except_id=None):
    if acronym_taken(db, acronym, except_id):
        raise Conflict(f"A series with the acronym {acronym} already exists")


def _check_year(db, series_id, year, except_id=None):
    if year_taken(db, series_id, year, except_id):
        raise Conflict(f"That series already has a {year} edition")


def _get(db: Session, entity: str, key: str):
    obj = db.get(MODELS[entity], key)
    if obj is None:
        name = "conference" if entity == "edition" else entity
        raise NotFound(f"No such {name}")
    return obj


# --- writes ----------------------------------------------------------------------


def create_series(db: Session, actor: Actor, data: SeriesIn) -> Series:
    _check_acronym(db, data.acronym)
    s = Series(**data.model_dump())
    _create(db, actor, "series", s)
    db.commit()
    return s


def update_series(db: Session, actor: Actor, series_id: str, data: SeriesIn) -> Series:
    s = _get(db, "series", series_id)
    _check_acronym(db, data.acronym, except_id=series_id)
    _update(db, actor, "series", s, data.model_dump())
    db.commit()
    return s


def delete_series(db: Session, actor: Actor, series_id: str) -> None:
    """Delete a series with no editions. Stars on it stay (they point nowhere and
    show nothing), so undoing the delete gives everyone their star back."""
    s = _get(db, "series", series_id)
    if s.editions:
        raise Conflict("Delete the series' editions first; this removes nothing else")
    _delete(db, actor, "series", s)
    db.commit()


def create_edition(db: Session, actor: Actor, series_id: str, data: EditionIn) -> Conference:
    _get(db, "series", series_id)
    _check_year(db, series_id, data.year)
    conf = Conference(series_id=series_id, **data.model_dump())
    _create(db, actor, "edition", conf)
    db.commit()
    return conf


def update_edition(db: Session, actor: Actor, conf_id: str, data: EditionIn) -> Conference:
    conf = _get(db, "edition", conf_id)
    _check_year(db, conf.series_id, data.year, except_id=conf_id)
    _update(db, actor, "edition", conf, data.model_dump())
    db.commit()
    return conf


def delete_edition(db: Session, actor: Actor, conf_id: str) -> None:
    """Delete an edition and its milestones; ideas registered for it lose their venue."""
    _delete(db, actor, "edition", _get(db, "edition", conf_id))
    db.commit()


def create_milestone(db: Session, actor: Actor, conf_id: str, data: MilestoneIn) -> Milestone:
    _get(db, "edition", conf_id)
    ms = Milestone(conference_id=conf_id, **data.model_dump())
    _create(db, actor, "milestone", ms)
    db.commit()
    return ms


def update_milestone(db: Session, actor: Actor, ms_id: str, data: MilestoneIn) -> Milestone:
    ms = _get(db, "milestone", ms_id)
    _update(db, actor, "milestone", ms, data.model_dump())
    db.commit()
    return ms


def delete_milestone(db: Session, actor: Actor, ms_id: str) -> None:
    _delete(db, actor, "milestone", _get(db, "milestone", ms_id))
    db.commit()


# --- undo ------------------------------------------------------------------------


def _restore(db: Session, actor: Actor, change: Change) -> Change:
    """Bring back what `change` deleted, under its old id."""
    entity, snap = change.entity, change.before
    values = _values(entity, snap)
    if entity == "series":
        _check_acronym(db, values["acronym"])
        return _create(db, actor, entity, Series(id=snap["id"], **values), change.id)

    if entity == "milestone":
        if db.get(Conference, snap["conference_id"]) is None:
            raise Conflict(f"{change.label}: its edition no longer exists; restore that first")
        obj = Milestone(id=snap["id"], conference_id=snap["conference_id"], **values)
        return _create(db, actor, entity, obj, change.id)

    if db.get(Series, snap["series_id"]) is None:
        raise Conflict(f"{change.label}: its series no longer exists; restore that first")
    _check_year(db, snap["series_id"], values["year"])
    conf = Conference(id=snap["id"], series_id=snap["series_id"], **values)
    restored = []
    for m in snap.get("milestones", []):
        if db.get(Milestone, m["id"]) is None:
            conf.milestones.append(Milestone(id=m["id"], **_values("milestone", m)))
            restored.append(m)
    for sub_id in snap.get("submission_ids", []):
        sub = db.get(Submission, sub_id)
        if sub is not None and sub.conference_id is None:
            sub.conference_id = conf.id
    return _create(db, actor, entity, conf, change.id, {"milestones": restored})


def undo(db: Session, actor: Actor, change_id: int) -> Change:
    # Lock the change row so two simultaneous undos of it cannot both apply.
    change = db.get(Change, change_id, with_for_update=True)
    if change is None:
        raise NotFound("No such change")
    if change.undone_by is not None:
        raise Conflict(f"Change {change_id} was already undone (by change {change.undone_by})")

    entity = change.entity
    current = db.get(MODELS[entity], change.entity_id)
    if change.action == "delete":
        if current is not None:
            raise Conflict(f"{change.label} exists again; nothing to restore")
        new = _restore(db, actor, change)
    else:
        if current is None:
            raise Conflict(f"{change.label} no longer exists")
        if snapshot(entity, current) != _shallow(change.after):
            raise Conflict(f"{change.label} has changed since; undo the later changes first")
        if change.action == "create":
            if entity == "series" and current.editions:
                raise Conflict(f"{change.label} has editions now; delete or undo those first")
            if entity == "edition" and current.submissions:
                raise Conflict(
                    f"{change.label} has ideas registered for it now; "
                    "move them to another edition first"
                )
            if entity == "edition":
                now = [snapshot("milestone", m) for m in current.milestones]
                then = (change.after or {}).get("milestones", [])
                if sorted(now, key=lambda m: m["id"]) != sorted(then, key=lambda m: m["id"]):
                    raise Conflict(
                        f"{change.label} has milestones added or edited since; "
                        "delete or undo those first"
                    )
            new = _delete(db, actor, entity, current, change.id)
        else:
            values = _values(entity, change.before)
            if entity == "series":
                _check_acronym(db, values["acronym"], except_id=current.id)
            if entity == "edition":
                _check_year(db, current.series_id, values["year"], except_id=current.id)
            new = _update(db, actor, entity, current, values, change.id)
            if new is None:
                raise Conflict(f"{change.label} already has its values from before")
    change.undone_by = new.id
    db.commit()
    return new
