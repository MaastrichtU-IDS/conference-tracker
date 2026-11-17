"""REST API consumed by the single-page frontend."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..auth import current_user, require_admin, require_user
from ..config import Settings, get_settings
from ..db import get_db
from ..models import Conference, Milestone, Star, Submission, User
from ..schemas import (
    BootstrapOut,
    ConferenceIn,
    ConferenceOut,
    MeOut,
    MilestoneIn,
    MilestoneOut,
    StarIn,
    SubmissionIn,
    SubmissionOut,
)

router = APIRouter(prefix="/api", tags=["api"])


def _conf_out(c: Conference) -> ConferenceOut:
    return ConferenceOut.model_validate(c)


def _ms_out(m: Milestone) -> MilestoneOut:
    out = MilestoneOut.model_validate(m)
    out.venue = m.conference.venue if m.conference else ""
    return out


def _sub_out(s: Submission) -> SubmissionOut:
    return SubmissionOut(
        id=s.id,
        conference_id=s.conference_id,
        venue=s.conference.venue if s.conference else None,
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
    confs = db.scalars(select(Conference).order_by(Conference.venue)).all()
    mss = db.scalars(
        select(Milestone).options(selectinload(Milestone.conference)).order_by(Milestone.date)
    ).all()

    subs: list[Submission] = []
    stars: dict[str, list[str]] = {"conference": [], "milestone": []}
    if user is not None:
        subs = list(_all_submissions(db))
        for s in db.scalars(select(Star).where(Star.user_id == user.id)):
            stars.setdefault(s.kind, []).append(s.target_id)

    return BootstrapOut(
        app_name=settings.app_name,
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
@router.get("/stars")
def list_stars(db: Session = Depends(get_db), user: User = Depends(require_user)) -> dict:
    out: dict[str, list[str]] = {"conference": [], "milestone": []}
    for s in db.scalars(select(Star).where(Star.user_id == user.id)):
        out.setdefault(s.kind, []).append(s.target_id)
    return out


@router.post("/stars")
def add_star(
    payload: StarIn, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    exists = db.scalar(
        select(Star).where(
            Star.user_id == user.id,
            Star.kind == payload.kind,
            Star.target_id == payload.target_id,
        )
    )
    if exists is None:
        db.add(Star(user_id=user.id, kind=payload.kind, target_id=payload.target_id))
        db.commit()
    return {"starred": True}


@router.delete("/stars")
def remove_star(
    kind: str, target_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    db.execute(
        delete(Star).where(Star.user_id == user.id, Star.kind == kind, Star.target_id == target_id)
    )
    db.commit()
    return {"starred": False}


@router.post("/feed-token/rotate")
def rotate_feed_token(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Invalidate the old calendar URL and issue a new one."""
    token = user.rotate_feed_token()
    db.commit()
    return {"feed_url": f"{settings.base_url}/feeds/{token}.ics"}


# --------------------------------------------------------------------------
# Conferences and milestones — admin only.
# --------------------------------------------------------------------------
@router.post("/conferences", response_model=ConferenceOut, status_code=status.HTTP_201_CREATED)
def create_conference(
    payload: ConferenceIn, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> ConferenceOut:
    if db.scalar(select(Conference).where(Conference.venue == payload.venue)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That venue already exists")
    conf = Conference(**payload.model_dump())
    db.add(conf)
    db.commit()
    db.refresh(conf)
    return _conf_out(conf)


@router.put("/conferences/{conf_id}", response_model=ConferenceOut)
def update_conference(
    conf_id: str,
    payload: ConferenceIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ConferenceOut:
    conf = db.get(Conference, conf_id)
    if conf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conference")
    for field, value in payload.model_dump().items():
        setattr(conf, field, value)
    db.commit()
    db.refresh(conf)
    return _conf_out(conf)


@router.delete(
    "/conferences/{conf_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_conference(
    conf_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> None:
    conf = db.get(Conference, conf_id)
    if conf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conference")
    db.delete(conf)
    db.commit()


@router.post(
    "/conferences/{conf_id}/milestones",
    response_model=MilestoneOut,
    status_code=status.HTTP_201_CREATED,
)
def create_milestone(
    conf_id: str,
    payload: MilestoneIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> MilestoneOut:
    conf = db.get(Conference, conf_id)
    if conf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conference")
    ms = Milestone(conference_id=conf_id, **payload.model_dump())
    db.add(ms)
    db.commit()
    db.refresh(ms)
    return _ms_out(ms)


@router.put("/milestones/{ms_id}", response_model=MilestoneOut)
def update_milestone(
    ms_id: str,
    payload: MilestoneIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> MilestoneOut:
    ms = db.get(Milestone, ms_id)
    if ms is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such milestone")
    for field, value in payload.model_dump().items():
        setattr(ms, field, value)
    db.commit()
    db.refresh(ms)
    return _ms_out(ms)


@router.delete(
    "/milestones/{ms_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_milestone(
    ms_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> None:
    ms = db.get(Milestone, ms_id)
    if ms is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such milestone")
    db.delete(ms)
    db.commit()


@router.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict:
    db.execute(select(1))
    return {"status": "ok", "time": dt.datetime.now(dt.UTC).isoformat()}
