"""Pydantic request/response models."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class MilestoneIn(BaseModel):
    date: dt.date
    end_date: dt.date | None = None
    track: str = ""
    milestone: str = ""
    time_basis: str = ""
    status: str = ""
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
    name: str = ""
    homepage: str = ""
    emphasis: str = ""
    fit: str = ""
    notes: str = ""


class SeriesOut(SeriesIn):
    id: str
    years: list[int] = Field(default_factory=list)
    # The edition to show for this series (app/editions.py) and whether it still
    # takes submissions. Not open = deadlines passed and no later edition is open.
    current_edition_id: str | None = None
    current_year: int | None = None
    open: bool = False


class EditionIn(BaseModel):
    """One year of a series."""

    year: int = Field(ge=1950, le=2100)
    location: str = ""
    site_key: str | None = None
    url: str = ""
    status: str = ""
    notes: str = ""
    source: str = ""
    abstract_date: dt.date | None = None
    paper_date: dt.date | None = None
    notify_date: dt.date | None = None
    camera_date: dt.date | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None


class ConferenceOut(EditionIn):
    """An edition, with its series' lasting facts alongside."""

    model_config = ConfigDict(from_attributes=True)
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
    track: str = ""
    lead: str = ""
    coauthors: str = ""
    status: str = "Idea"
    project: str = ""
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
