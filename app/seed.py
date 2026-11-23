"""Load the bundled conference dataset into an empty database."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Conference, Milestone, Series

log = logging.getLogger(__name__)

# Maps a conference's free-text location onto a point in app/static/map.json.
SITE_OF: dict[str, str] = {
    "Montréal, Canada": "Montréal, Canada",
    "California, USA": "California, USA",
    "Limassol, Cyprus": "Limassol, Cyprus",
    "Kyoto, Japan": "Kyoto, Japan",
    "Copenhagen, Denmark": "Copenhagen, Denmark",
    "Rio de Janeiro, Brazil": "Rio de Janeiro, Brazil",
    "Nijmegen, Netherlands": "Nijmegen, Netherlands",
    "Amsterdam, Netherlands": "Amsterdam, Netherlands",
    "Athens, Greece": "Athens, Greece",
    "Buenos Aires, Argentina": "Buenos Aires, Argentina",
    "Bratislava, Slovakia": "Bratislava, Slovakia",
    "Bologna, Italy": "Bologna, Italy",
    "Montevideo, Uruguay": "Montevideo, Uruguay",
    "Carnegie Mellon University, USA": "Pittsburgh, USA",
    "Carnegie Mellon University, Pittsburgh, USA": "Pittsburgh, USA",
    "Basel, Switzerland": "Basel, Switzerland",
    "Tallinn, Estonia": "Tallinn, Estonia",
    "Atlanta, USA": "Atlanta, USA",
    "Dubai, UAE": "Dubai, UAE",
    "San Diego, USA": "San Diego, USA",
}


def _date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


# Rows without a year or any event date belong to the 2027 cycle the bundled
# dataset was compiled for (the same rule as migration 0002).
LEGACY_YEAR = 2027


def _edition_year(row: dict) -> int:
    if row.get("year"):
        return int(row["year"])
    for key in ("start", "end"):
        if row.get(key):
            return int(row[key][:4])
    return LEGACY_YEAR


def seed_if_empty(db: Session, seed_file: str | Path) -> int:
    """Insert the seed dataset when no series exists yet.

    Each seed row is one edition: its `venue` names the series (created on first
    sight, with the row's name, emphasis and fit), its `year` (or event date)
    the edition. Returns the number of editions created (0 when the database
    already holds data, so this is safe to run on every boot).
    """
    if db.scalar(select(Series).limit(1)) is not None:
        return 0

    path = Path(seed_file)
    if not path.exists():
        log.warning("seed file %s not found; starting empty", path)
        return 0

    payload = json.loads(path.read_text(encoding="utf-8"))
    series: dict[str, Series] = {}
    by_venue: dict[str, Conference] = {}  # latest edition seen per venue
    by_edition: dict[tuple[str, int], Conference] = {}

    for row in payload.get("conferences", []):
        venue = row["venue"]
        if venue not in series:
            series[venue] = Series(
                acronym=venue,
                name=row.get("name") or "",
                emphasis=row.get("emphasis") or "",
                fit=row.get("fit") or "",
            )
            db.add(series[venue])
        conf = Conference(
            series=series[venue],
            year=_edition_year(row),
            location=row.get("location") or "",
            site_key=SITE_OF.get(row.get("location") or ""),
            url=row.get("url") or "",
            status=row.get("status") or "",
            notes=row.get("notes") or "",
            source=row.get("source") or "",
            abstract_date=_date(row.get("abstract")),
            paper_date=_date(row.get("paper")),
            notify_date=_date(row.get("notify")),
            camera_date=_date(row.get("camera")),
            start_date=_date(row.get("start")),
            end_date=_date(row.get("end")),
        )
        db.add(conf)
        by_venue[venue] = conf
        by_edition[(venue, conf.year)] = conf

    db.flush()

    milestones = 0
    for row in payload.get("milestones", []):
        if row.get("year"):
            conf = by_edition.get((row.get("venue"), int(row["year"])))
        else:
            conf = by_venue.get(row.get("venue"))
        if conf is None or not row.get("date"):
            continue
        db.add(
            Milestone(
                conference_id=conf.id,
                date=_date(row["date"]),
                end_date=_date(row.get("end")),
                track=row.get("track") or "",
                milestone=row.get("milestone") or "",
                time_basis=row.get("time") or "",
                status=row.get("status") or "",
                notes=row.get("notes") or "",
            )
        )
        milestones += 1

    db.commit()
    log.info("seeded %d editions and %d milestones", len(by_venue), milestones)
    return len(by_venue)
