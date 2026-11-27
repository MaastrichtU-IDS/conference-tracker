"""Pydantic request/response models."""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_WEB_URL = re.compile(r"https?://\S", re.I)


def _web_url(value: str) -> str:
    if value and not _WEB_URL.match(value):
        raise ValueError("must be empty or start with http:// or https://")
    return value


# A link the app renders as <a href>: empty, or an http(s) address. Anything else
# (javascript:, data:, a bare host) is refused, whoever writes it. Input only.
WebUrl = Annotated[str, Field(max_length=500), AfterValidator(_web_url)]


def _upto(n: int):
    """Text that fits its column (app/models.py): too long is refused on the way in."""
    return Annotated[str, Field(max_length=n)]


class MilestoneIn(BaseModel):
    date: dt.date
    end_date: dt.date | None = None
    track: _upto(250) = ""
    milestone: _upto(250) = ""
    time_basis: _upto(120) = ""
    status: _upto(200) = ""
    notes: str = ""


class MilestoneOut(MilestoneIn):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conference_id: str
    series_id: str = ""
    venue: str = ""  # the series acronym
    year: int | None = None
    label: str = ""  # "ESWC 2027"


class SeriesIn(BaseModel):
    """A recurring conference: what stays the same from one year to the next."""

    acronym: str = Field(min_length=1, max_length=80, pattern=r"^\S(.*\S)?$")
    name: _upto(400) = ""
    homepage: WebUrl = ""
    emphasis: str = ""
    fit: str = ""
    notes: str = ""


class SeriesOut(SeriesIn):
    homepage: str = ""  # checked on the way in only: never fail a read on stored data
    id: str
    years: list[int] = Field(default_factory=list)
    # The edition to show for this series (app/editions.py) and whether it still
    # takes submissions. Not open = deadlines passed and no later edition is open.
    current_edition_id: str | None = None
    current_year: int | None = None
    open: bool = False
    # "open" | "closed" (deadlines passed, next edition not in the tracker yet)
    # | "awaiting" (no submission deadline known yet)
    state: str = "awaiting"


class EditionIn(BaseModel):
    """One year of a series."""

    year: int = Field(ge=1950, le=2100)
    location: _upto(200) = ""
    site_key: _upto(120) | None = None
    url: WebUrl = ""
    status: _upto(200) = ""
    notes: str = ""
    source: _upto(200) = ""
    abstract_date: dt.date | None = None
    paper_date: dt.date | None = None
    notify_date: dt.date | None = None
    camera_date: dt.date | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None


class ConferenceOut(EditionIn):
    """An edition, with its series' lasting facts alongside."""

    model_config = ConfigDict(from_attributes=True)
    url: str = ""  # checked on the way in only: never fail a read on stored data
    id: str
    series_id: str
    venue: str  # the series acronym
    name: str = ""
    emphasis: str = ""
    fit: str = ""
    label: str  # "ESWC 2027"


class EditionDetail(ConferenceOut):
    milestones: list[MilestoneOut] = Field(default_factory=list)


class SeriesDetail(SeriesOut):
    editions: list[EditionDetail] = Field(default_factory=list)


class SubmissionIn(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    conference_id: str | None = None
    track: _upto(200) = ""
    lead: _upto(250) = ""
    coauthors: _upto(600) = ""
    status: _upto(60) = "Idea"
    project: _upto(120) = ""
    abstract: str = ""
    notes: str = ""
    links: list[str] = Field(default_factory=list)


class SubmissionOut(SubmissionIn):
    id: str
    venue: str | None = None  # the series acronym
    series_id: str | None = None
    edition: str | None = None  # "ESWC 2027"
    created_by: str | None = None
    updated_by: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class StarIn(BaseModel):
    # "conference" (an edition id) is still accepted from old pages and stored as
    # a star on that edition's series.
    kind: str = Field(pattern="^(series|milestone|conference)$")
    target_id: str


class MeOut(BaseModel):
    authenticated: bool
    auth_enabled: bool
    id: str | None = None
    email: str | None = None
    name: str | None = None
    is_admin: bool = False
    feed_url: str | None = None


class BootstrapOut(BaseModel):
    app_name: str
    series: list[SeriesOut]
    conferences: list[ConferenceOut]
    milestones: list[MilestoneOut]
    submissions: list[SubmissionOut]
    stars: dict[str, list[str]]
    me: MeOut


class TokenIn(BaseModel):
    name: str = Field(
        min_length=1, max_length=120, description="What it is for, e.g. 'monthly AI routine'"
    )
    expires_in_days: int = Field(90, ge=1, le=365)


class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str  # the first characters, to recognise it
    owner: str
    created_at: dt.datetime
    expires_at: dt.datetime
    last_used_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None


class TokenCreated(TokenOut):
    token: str  # shown this once; only a hash is kept


class ChangeOut(BaseModel):
    id: int
    at: dt.datetime
    actor: str
    via: str  # "web" | "token"
    token: str = ""  # the token's name when via == "token"
    entity: str  # "series" | "edition" | "milestone"
    entity_id: str
    series_id: str | None = None
    action: str  # "create" | "update" | "delete"
    label: str  # "ESWC 2027 · Paper submission (Research)"
    fields: list[str] = Field(default_factory=list)  # what an update changed
    before: dict | None = None
    after: dict | None = None
    source: str = ""
    note: str = ""
    undo_of: int | None = None
    undone_by: int | None = None
