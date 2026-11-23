"""iCalendar generation.

All entries are all-day VEVENTs marked free, with stable UIDs so a re-import
or a feed refresh updates in place instead of duplicating.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable

from .models import Conference, Milestone

PRODID = "-//MaastrichtU-IDS//Conference Tracker//EN"

# Milestone kinds that belong on a calendar when a whole conference is starred.
LIFECYCLE_PATTERNS = (
    "abstract",
    "full paper",
    "paper submission",
    "poster submission",
    "revised paper",
    "camera-ready",
    "notification",
    "rebuttal",
    "decisions",
)


def is_lifecycle(m: Milestone) -> bool:
    text = f"{m.milestone} {m.track}".lower()
    if m.time_basis == "Event local dates" or m.track == "Event":
        return False
    return any(p in text for p in LIFECYCLE_PATTERNS)


def _esc(value: str | None) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\r\n", r"\n")
        .replace("\n", r"\n")
    )


def _fold(line: str) -> str:
    """Fold to <=75 octets per RFC 5545, never splitting a character."""
    out: list[str] = []
    cur, size = "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 72:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n\t".join(out)


def _uid(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + "@conference-tracker"


def _day(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def _next_day(value: dt.date) -> str:
    return (value + dt.timedelta(days=1)).strftime("%Y%m%d")


def _event(
    *,
    key: str,
    start: dt.date,
    end: dt.date,
    summary: str,
    stamp: str,
    location: str = "",
    description: str = "",
    url: str = "",
    category: str = "Deadline",
) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_uid(key)}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{_day(start)}",
        f"DTEND;VALUE=DATE:{_next_day(end)}",
        _fold(f"SUMMARY:{_esc(summary)}"),
    ]
    if location:
        lines.append(_fold(f"LOCATION:{_esc(location)}"))
    if description:
        lines.append(_fold(f"DESCRIPTION:{_esc(description)}"))
    if url:
        lines.append(_fold(f"URL:{url}"))
    lines += ["TRANSP:TRANSPARENT", f"CATEGORIES:{category}", "END:VEVENT"]
    return lines


def conference_event(conf: Conference, stamp: str) -> list[str]:
    return _event(
        key=f"conference:{conf.id}",
        start=conf.start_date,
        end=conf.end_date or conf.start_date,
        summary=f"{conf.label} — {conf.name}" if conf.name else conf.label,
        stamp=stamp,
        location=conf.location,
        description=conf.emphasis,
        url=conf.url,
        category="Conference",
    )


def milestone_event(m: Milestone, stamp: str) -> list[str]:
    venue = m.conference.label if m.conference else ""
    track = m.track or ""
    summary = f"{venue} — {m.milestone}"
    if track and not track.lower().startswith("main"):
        summary += f" ({track})"
    bits = [
        m.time_basis if m.time_basis and m.time_basis != "Not specified" else "",
        m.status if m.status and m.status != "Published" else "",
        m.notes or "",
    ]
    return _event(
        key=f"milestone:{m.id}",
        start=m.date,
        end=m.end_date or m.date,
        summary=summary,
        stamp=stamp,
        description=" · ".join(x for x in bits if x),
        url=m.conference.url if m.conference else "",
        category="Conference" if m.track == "Event" else "Deadline",
    )


def build_calendar(
    *, name: str, conferences: Iterable[Conference], milestones: Iterable[Milestone]
) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:{_esc(name)}"),
        "X-WR-TIMEZONE:UTC",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    seen: set[str] = set()
    for conf in conferences:
        if conf.start_date and conf.id not in seen:
            seen.add(conf.id)
            lines += conference_event(conf, stamp)
    for m in milestones:
        if m.id in seen:
            continue
        seen.add(m.id)
        lines += milestone_event(m, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
