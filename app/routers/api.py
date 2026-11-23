"""REST API consumed by the single-page frontend."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import editing
from ..auth import (
    aware,
    current_token,
    current_user,
    hash_token,
    new_token,
    require_admin,
    require_session_admin,
    require_session_user,
    require_user,
)
from ..config import Settings, get_settings
from ..db import get_db
from ..editing import Actor
from ..editions import aoe_today, current_edition, edition_state
from ..models import ApiToken, Change, Conference, Milestone, Series, Star, Submission, User
from ..schemas import (
    BootstrapOut,
    ChangeOut,
    ConferenceOut,
    EditionDetail,
    EditionIn,
    MeOut,
    MilestoneIn,
    MilestoneOut,
    SeriesDetail,
    SeriesIn,
    SeriesOut,
    StarIn,
    SubmissionIn,
    SubmissionOut,
    TokenCreated,
    TokenIn,
    TokenOut,
)

router = APIRouter(prefix="/api", tags=["api"])


def _conf_out(c: Conference) -> ConferenceOut:
    return ConferenceOut.model_validate(c)


def _ms_out(m: Milestone) -> MilestoneOut:
    out = MilestoneOut.model_validate(m)
    if m.conference:
        out.series_id = m.conference.series_id
        out.venue = m.conference.venue
        out.year = m.conference.year
        out.label = m.conference.label
    return out


def _series_out(s: Series, today: dt.date, cls=SeriesOut):
    shown, is_open = current_edition(s.editions, today)
    return cls(
        id=s.id,
        acronym=s.acronym,
        name=s.name,
        homepage=s.homepage,
        emphasis=s.emphasis,
        fit=s.fit,
        notes=s.notes,
        years=[e.year for e in s.editions],
        current_edition_id=shown.id if shown else None,
        current_year=shown.year if shown else None,
        open=is_open,
        state=edition_state(shown, today),
    )


def _series_detail(s: Series, today: dt.date) -> SeriesDetail:
    out = _series_out(s, today, SeriesDetail)
    out.editions = [
        EditionDetail(**_conf_out(e).model_dump(), milestones=[_ms_out(m) for m in e.milestones])
        for e in s.editions
    ]
    return out


def _series_query():
    return select(Series).options(selectinload(Series.editions).selectinload(Conference.milestones))


def _find_series(db: Session, key: str) -> Series | None:
    """By id or, case-insensitively, by acronym."""
    return db.scalar(
        _series_query().where(or_(Series.id == key, func.lower(Series.acronym) == key.lower()))
    )


def _sub_out(s: Submission) -> SubmissionOut:
    return SubmissionOut(
        id=s.id,
        conference_id=s.conference_id,
        venue=s.conference.venue if s.conference else None,
        series_id=s.conference.series_id if s.conference else None,
        edition=s.conference.label if s.conference else None,
        title=s.title,
        track=s.track,
        lead=s.lead,
        coauthors=s.coauthors,
        status=s.status,
        project=s.project,
        abstract=s.abstract,
        notes=s.notes,
        links=s.link_list,
        created_by=s.created_by.name if s.created_by else None,
        updated_by=s.updated_by.name if s.updated_by else None,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _all_submissions(db: Session):
    return db.scalars(
        select(Submission)
        .options(
            selectinload(Submission.conference),
            selectinload(Submission.created_by),
            selectinload(Submission.updated_by),
        )
        .order_by(Submission.updated_at.desc())
    ).all()


def _me_out(user: User | None, settings: Settings) -> MeOut:
    if user is None:
        return MeOut(authenticated=False, auth_enabled=settings.auth_enabled)
    return MeOut(
        authenticated=True,
        auth_enabled=settings.auth_enabled,
        id=user.id,
        email=user.email,
        name=user.name,
        is_admin=user.is_admin,
        feed_url=f"{settings.base_url}/feeds/{user.feed_token}.ics",
    )


@router.get("/me", response_model=MeOut)
def me(
    user: User | None = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> MeOut:
    return _me_out(user, settings)


@router.get("/bootstrap", response_model=BootstrapOut)
def bootstrap(
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> BootstrapOut:
    """Everything the frontend needs for a first paint, in one round trip.

    Conferences and milestones are public deadlines. Submissions are members-only:
    an anonymous caller gets an empty list.
    """
    today = aoe_today()
    series = db.scalars(_series_query().order_by(Series.acronym)).all()
    confs = [e for s in series for e in s.editions]
    mss = sorted((m for e in confs for m in e.milestones), key=lambda m: m.date)

    subs: list[Submission] = []
    stars: dict[str, list[str]] = {"series": [], "milestone": []}
    if user is not None:
        subs = list(_all_submissions(db))
        stars = _stars_of(db, user)

    return BootstrapOut(
        app_name=settings.app_name,
        series=[_series_out(s, today) for s in series],
        conferences=[_conf_out(c) for c in confs],
        milestones=[_ms_out(m) for m in mss],
        submissions=[_sub_out(s) for s in subs],
        stars=stars,
        me=_me_out(user, settings),
    )


# --------------------------------------------------------------------------
# Submissions — members only: any signed-in user may read, add and edit.
# --------------------------------------------------------------------------
@router.get("/submissions", response_model=list[SubmissionOut])
def list_submissions(
    db: Session = Depends(get_db), _: User = Depends(require_user)
) -> list[SubmissionOut]:
    return [_sub_out(s) for s in _all_submissions(db)]


@router.post("/submissions", response_model=SubmissionOut, status_code=status.HTTP_201_CREATED)
def create_submission(
    payload: SubmissionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> SubmissionOut:
    sub = Submission(
        **payload.model_dump(exclude={"links"}),
        links="\n".join(payload.links),
        created_by_id=user.id,
        updated_by_id=user.id,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return _sub_out(sub)


@router.put("/submissions/{sub_id}", response_model=SubmissionOut)
def update_submission(
    sub_id: str,
    payload: SubmissionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> SubmissionOut:
    sub = db.get(Submission, sub_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such submission")
    for field, value in payload.model_dump(exclude={"links"}).items():
        setattr(sub, field, value)
    sub.links = "\n".join(payload.links)
    sub.updated_by_id = user.id
    db.commit()
    db.refresh(sub)
    return _sub_out(sub)


@router.delete(
    "/submissions/{sub_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_submission(
    sub_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> None:
    sub = db.get(Submission, sub_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such submission")
    db.delete(sub)
    db.commit()


# --------------------------------------------------------------------------
# Stars — private to each user.
# --------------------------------------------------------------------------
def _stars_of(db: Session, user: User) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"series": [], "milestone": []}
    for s in db.scalars(select(Star).where(Star.user_id == user.id)):
        out.setdefault(s.kind, []).append(s.target_id)
    return out


def _star_target(db: Session, kind: str, target_id: str) -> tuple[str, str]:
    """Normalise a star: a "conference" (edition) star becomes a star on its series."""
    if kind == "conference":
        conf = db.get(Conference, target_id)
        if conf is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conference")
        return "series", conf.series_id
    return kind, target_id


@router.get("/stars")
def list_stars(db: Session = Depends(get_db), user: User = Depends(require_user)) -> dict:
    return _stars_of(db, user)


@router.post("/stars")
def add_star(
    payload: StarIn, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    kind, target_id = _star_target(db, payload.kind, payload.target_id)
    exists = db.scalar(
        select(Star).where(Star.user_id == user.id, Star.kind == kind, Star.target_id == target_id)
    )
    if exists is None:
        db.add(Star(user_id=user.id, kind=kind, target_id=target_id))
        db.commit()
    return {"starred": True}


@router.delete("/stars")
def remove_star(
    kind: str, target_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    kind, target_id = _star_target(db, kind, target_id)
    db.execute(
        delete(Star).where(Star.user_id == user.id, Star.kind == kind, Star.target_id == target_id)
    )
    db.commit()
    return {"starred": False}


@router.post("/feed-token/rotate")
def rotate_feed_token(
    db: Session = Depends(get_db),
    user: User = Depends(require_session_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Invalidate the old calendar URL and issue a new one."""
    token = user.rotate_feed_token()
    db.commit()
    return {"feed_url": f"{settings.base_url}/feeds/{token}.ics"}


# --------------------------------------------------------------------------
# Series and editions — anyone may read (they are public deadlines); admins write.
# --------------------------------------------------------------------------
@router.get("/series", response_model=list[SeriesOut])
def search_series(
    q: str = Query("", description="Acronym or name, case-insensitive; empty lists every series"),
    db: Session = Depends(get_db),
) -> list[SeriesOut]:
    """Series matching `q`, each with the edition to show for it ("the next ESWC")."""
    query = _series_query()
    needle = q.strip().lower()
    if needle:
        like = f"%{needle}%"
        query = query.where(
            or_(func.lower(Series.acronym).like(like), func.lower(Series.name).like(like))
        )
    found = db.scalars(query).all()
    # Exact acronym first, then acronyms that start with the query, then the rest.
    found = sorted(
        found,
        key=lambda s: (
            s.acronym.lower() != needle,
            not s.acronym.lower().startswith(needle),
            s.acronym.lower(),
        ),
    )
    today = aoe_today()
    return [_series_out(s, today) for s in found]


@router.get("/series/{key}", response_model=SeriesDetail)
def get_series(key: str, db: Session = Depends(get_db)) -> SeriesDetail:
    """One series by id or acronym, with every edition and its milestones."""
    s = _find_series(db, key)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such series")
    return _series_detail(s, aoe_today())


# Writes go through app/editing.py, which logs each one in `changes`. Any write
# may carry ?source=<where the value was read> and ?note=<why>; both are logged.
def editor(
    request: Request,
    source: str = Query("", max_length=1000, description="Where the new value comes from"),
    note: str = Query("", max_length=2000, description="Why, for the change log"),
    user: User = Depends(require_admin),
) -> Actor:
    return Actor(user=user, token=current_token(request), source=source, note=note)


DELETED = {
    "status_code": status.HTTP_204_NO_CONTENT,
    "response_class": Response,
    "response_model": None,
}


@router.post("/series", response_model=SeriesOut, status_code=status.HTTP_201_CREATED)
def create_series(
    payload: SeriesIn, db: Session = Depends(get_db), actor: Actor = Depends(editor)
) -> SeriesOut:
    s = editing.create_series(db, actor, payload)
    return _series_out(_find_series(db, s.id), aoe_today())


@router.put("/series/{series_id}", response_model=SeriesOut)
def update_series(
    series_id: str,
    payload: SeriesIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(editor),
) -> SeriesOut:
    editing.update_series(db, actor, series_id, payload)
    return _series_out(_find_series(db, series_id), aoe_today())


@router.delete("/series/{series_id}", **DELETED)
def delete_series(
    series_id: str, db: Session = Depends(get_db), actor: Actor = Depends(editor)
) -> None:
    editing.delete_series(db, actor, series_id)


@router.post(
    "/series/{series_id}/editions",
    response_model=ConferenceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_edition(
    series_id: str,
    payload: EditionIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(editor),
) -> ConferenceOut:
    return _conf_out(editing.create_edition(db, actor, series_id, payload))


@router.put("/conferences/{conf_id}", response_model=ConferenceOut)
def update_conference(
    conf_id: str,
    payload: EditionIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(editor),
) -> ConferenceOut:
    """Update one edition."""
    return _conf_out(editing.update_edition(db, actor, conf_id, payload))


@router.delete("/conferences/{conf_id}", **DELETED)
def delete_conference(
    conf_id: str, db: Session = Depends(get_db), actor: Actor = Depends(editor)
) -> None:
    """Delete one edition and its milestones; submissions to it lose their venue."""
    editing.delete_edition(db, actor, conf_id)


@router.post(
    "/conferences/{conf_id}/milestones",
    response_model=MilestoneOut,
    status_code=status.HTTP_201_CREATED,
)
def create_milestone(
    conf_id: str,
    payload: MilestoneIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(editor),
) -> MilestoneOut:
    return _ms_out(editing.create_milestone(db, actor, conf_id, payload))


@router.put("/milestones/{ms_id}", response_model=MilestoneOut)
def update_milestone(
    ms_id: str,
    payload: MilestoneIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(editor),
) -> MilestoneOut:
    return _ms_out(editing.update_milestone(db, actor, ms_id, payload))


@router.delete("/milestones/{ms_id}", **DELETED)
def delete_milestone(
    ms_id: str, db: Session = Depends(get_db), actor: Actor = Depends(editor)
) -> None:
    editing.delete_milestone(db, actor, ms_id)


# --------------------------------------------------------------------------
# Change log and undo — admins (in the app or with a token).
# --------------------------------------------------------------------------
def _change_out(c: Change) -> ChangeOut:
    return ChangeOut(
        id=c.id,
        at=aware(c.at),
        actor=c.actor_name,
        via=c.via,
        token=c.token_name,
        entity=c.entity,
        entity_id=c.entity_id,
        series_id=c.series_id,
        action=c.action,
        label=c.label,
        fields=editing.changed_fields(c.before, c.after),
        before=c.before,
        after=c.after,
        source=c.source,
        note=c.note,
        undo_of=c.undo_of,
        undone_by=c.undone_by,
    )


@router.get("/changes", response_model=list[ChangeOut])
def list_changes(
    series: str = Query("", description="Series id or acronym: only changes to it"),
    entity_id: str = Query("", description="Only changes to this series, edition or milestone"),
    before: int | None = Query(None, description="Page: changes older than this id"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[ChangeOut]:
    """Newest first."""
    q = select(Change).order_by(Change.id.desc()).limit(limit)
    if series:
        found = db.scalar(
            select(Series.id).where(
                or_(Series.id == series, func.lower(Series.acronym) == series.lower())
            )
        )
        q = q.where(Change.series_id == (found or series))
    if entity_id:
        q = q.where(Change.entity_id == entity_id)
    if before is not None:
        q = q.where(Change.id < before)
    return [_change_out(c) for c in db.scalars(q)]


@router.post("/changes/{change_id}/undo", response_model=ChangeOut)
def undo_change(
    change_id: int, db: Session = Depends(get_db), actor: Actor = Depends(editor)
) -> ChangeOut:
    """Revert one change, if nothing has touched that record since. Returns the new change."""
    return _change_out(editing.undo(db, actor, change_id))


# --------------------------------------------------------------------------
# API tokens — managed by a signed-in admin, never with a token.
# --------------------------------------------------------------------------
def _token_out(t: ApiToken, cls=TokenOut, **extra):
    return cls(
        id=t.id,
        name=t.name,
        prefix=t.prefix,
        owner=t.user.name or t.user.email if t.user else "",
        created_at=aware(t.created_at),
        expires_at=aware(t.expires_at),
        last_used_at=aware(t.last_used_at),
        revoked_at=aware(t.revoked_at),
        **extra,
    )


@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(
    db: Session = Depends(get_db), _: User = Depends(require_session_admin)
) -> list[TokenOut]:
    """Every admin's tokens, so anyone of them can revoke a token that should go."""
    tokens = db.scalars(select(ApiToken).order_by(ApiToken.created_at.desc()))
    return [_token_out(t) for t in tokens]


@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(
    payload: TokenIn, db: Session = Depends(get_db), user: User = Depends(require_session_admin)
) -> TokenCreated:
    """A token acting as you. Its value is in this response only."""
    raw = new_token()
    now = dt.datetime.now(dt.UTC)
    t = ApiToken(
        user_id=user.id,
        name=payload.name.strip(),
        prefix=raw[:10],
        token_hash=hash_token(raw),
        created_at=now,
        expires_at=now + dt.timedelta(days=payload.expires_in_days),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _token_out(t, TokenCreated, token=raw)


@router.delete("/tokens/{token_id}", **DELETED)
def revoke_token(
    token_id: str, db: Session = Depends(get_db), _: User = Depends(require_session_admin)
) -> None:
    t = db.get(ApiToken, token_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such token")
    if t.revoked_at is None:
        t.revoked_at = dt.datetime.now(dt.UTC)
        db.commit()


@router.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict:
    db.execute(select(1))
    return {"status": "ok", "time": dt.datetime.now(dt.UTC).isoformat()}
