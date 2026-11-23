"""Which edition of a series is the one to show: the "next edition" rule.

A series (ESWC) has yearly editions (ESWC 2027, ESWC 2028). Searching for the
series should land on the edition you can still submit to:

1. the earliest edition with a submission deadline (abstract, paper or
   proposal) still ahead, counted in AoE (UTC-12);
2. otherwise the latest edition, flagged "not open": its deadlines have passed
   (or it has no dates yet) and no later edition is open.

`milestone_kind` mirrors kindOf() in app/static/app.js so the server and the UI
agree on what counts as a submission deadline.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from typing import Any

SUBMISSION_KINDS = {"abstract", "paper", "proposal"}

_OPENS = re.compile(r"\bopens?\b", re.I)
_ABSTRACT = re.compile(r"abstract", re.I)
_PAPER = re.compile(
    r"full paper|paper submission|poster submission|revised paper|supplementary|^submission\b", re.I
)
_NOTIFY = re.compile(r"notification|notices sent|reviews released|decisions", re.I)
_FINAL = re.compile(
    r"camera-ready|materials due|website due|revised version|draft presentation|online materials",
    re.I,
)
_PROPOSAL = re.compile(r"proposal", re.I)
_REVIEW = re.compile(r"rebuttal|feedback|discussion|meta-review|author response", re.I)


def aoe_today(now: dt.datetime | None = None) -> dt.date:
    """Today in Anywhere-on-Earth time: a deadline is open until 23:59 UTC-12 on its date."""
    now = now or dt.datetime.now(dt.UTC)
    return (now.astimezone(dt.UTC) - dt.timedelta(hours=12)).date()


def milestone_kind(m: Any) -> str:
    text = f"{m.milestone or ''} {m.track or ''}"
    name = m.milestone or ""
    if m.time_basis == "Event local dates" or m.track == "Event":
        return "event"
    if _OPENS.search(text):
        return "admin"
    if _ABSTRACT.search(text):
        return "abstract"
    if _PAPER.search(name):
        return "paper"
    if _NOTIFY.search(text):
        return "notification"
    if _FINAL.search(text):
        return "final"
    if _PROPOSAL.search(text):
        return "proposal"
    if _REVIEW.search(text):
        return "review"
    return "admin"


def has_open_deadline(edition: Any, today: dt.date) -> bool:
    for d in (edition.abstract_date, edition.paper_date):
        if d is not None and d >= today:
            return True
    for m in edition.milestones:
        if milestone_kind(m) in SUBMISSION_KINDS and (m.end_date or m.date) >= today:
            return True
    return False


def current_edition(editions: Iterable[Any], today: dt.date) -> tuple[Any | None, bool]:
    """(edition to show, whether it is open for submissions)."""
    ordered = sorted(editions, key=lambda e: e.year)
    for e in ordered:
        if has_open_deadline(e, today):
            return e, True
    return (ordered[-1] if ordered else None), False
