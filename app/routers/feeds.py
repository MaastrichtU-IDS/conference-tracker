"""Calendar feeds.

`/feeds/{token}.ics` is deliberately unauthenticated: Outlook and Google fetch
it with no credentials. The token is a 32-byte secret, is the only thing that
identifies the calendar, and can be rotated from the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings, get_settings
from ..db import get_db
from ..ics import build_calendar, is_lifecycle
from ..models import Conference, Milestone, Series, Star, User

router = APIRouter(prefix="/feeds", tags=["feeds"])

CACHE_HEADERS = {"Cache-Control": "public, max-age=1800", "X-Robots-Tag": "noindex"}


def _ics(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={**CACHE_HEADERS, "Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/all.ics")
def all_feed(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Response:
    """Every conference and every dated milestone."""
    confs = db.scalars(select(Conference)).all()
    mss = db.scalars(
        select(Milestone).options(selectinload(Milestone.conference)).order_by(Milestone.date)
    ).all()
    body = build_calendar(
        name=f"{settings.app_name} — all events", conferences=confs, milestones=mss
    )
    return _ics(body, "conference-tracker-all.ics")


@router.get("/{token}.ics")
def starred_feed(
    token: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> Response:
    """One person's starred series (every edition) and milestones."""
    user = db.scalar(select(User).where(User.feed_token == token))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown calendar")

    stars = db.scalars(select(Star).where(Star.user_id == user.id)).all()
    series_ids = {s.target_id for s in stars if s.kind == "series"}
    ms_ids = {s.target_id for s in stars if s.kind == "milestone"}

    confs = (
        db.scalars(
            select(Conference)
            .join(Series)
            .options(selectinload(Conference.milestones))
            .where(Conference.series_id.in_(series_ids))
        ).all()
        if series_ids
        else []
    )

    milestones: list[Milestone] = []
    for conf in confs:
        milestones += [m for m in conf.milestones if is_lifecycle(m)]
    if ms_ids:
        milestones += db.scalars(
            select(Milestone)
            .options(selectinload(Milestone.conference))
            .where(Milestone.id.in_(ms_ids))
        ).all()

    milestones.sort(key=lambda m: m.date)
    body = build_calendar(
        name=f"{settings.app_name} — {user.name or user.email}",
        conferences=confs,
        milestones=milestones,
    )
    return _ics(body, "conference-tracker.ics")
