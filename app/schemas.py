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
    venue: str = ""


class ConferenceIn(BaseModel):
    venue: str = Field(min_length=1, max_length=80)
    name: str = ""
    location: str = ""
    site_key: str | None = None
    url: str = ""
    status: str = ""
    emphasis: str = ""
    fit: str = ""
    notes: str = ""
    source: str = ""
    abstract_date: dt.date | None = None
    paper_date: dt.date | None = None
    notify_date: dt.date | None = None
    camera_date: dt.date | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None


class ConferenceOut(ConferenceIn):
    model_config = ConfigDict(from_attributes=True)
    id: str


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
    venue: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class StarIn(BaseModel):
    kind: str = Field(pattern="^(conference|milestone)$")
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
    conferences: list[ConferenceOut]
    milestones: list[MilestoneOut]
    submissions: list[SubmissionOut]
    stars: dict[str, list[str]]
    me: MeOut
