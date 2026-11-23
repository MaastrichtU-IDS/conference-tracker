"""SQLAlchemy models.

Series, editions (conferences) and milestones are reference data, editable by
admins in the app or with an API token; every such write is logged in `changes`.
Submissions are shared across everyone. Stars are private to each user.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    feed_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(32)
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    stars: Mapped[list[Star]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def rotate_feed_token(self) -> str:
        self.feed_token = secrets.token_urlsafe(32)
        return self.feed_token


class Series(Base):
    """A recurring conference (ESWC), holding what stays the same across years."""

    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    acronym: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(400), default="")
    homepage: Mapped[str] = mapped_column(String(500), default="")
    emphasis: Mapped[str] = mapped_column(Text, default="")
    fit: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    editions: Mapped[list[Conference]] = relationship(
        back_populates="series", order_by="Conference.year"
    )


class Conference(Base):
    """One edition of a series (ESWC 2027). Its id is what milestones, submissions
    and calendar UIDs point at, so it never changes."""

    __tablename__ = "conferences"
    __table_args__ = (UniqueConstraint("series_id", "year", name="uq_conference_series_year"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    series_id: Mapped[str] = mapped_column(
        ForeignKey("series.id", ondelete="RESTRICT", name="conferences_series_id_fkey"),
        index=True,
    )
    year: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(String(200), default="")
    site_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")

    # Headline dates, denormalised for the conference cards.
    abstract_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    paper_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    notify_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    camera_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    series: Mapped[Series] = relationship(back_populates="editions", lazy="joined")
    milestones: Mapped[list[Milestone]] = relationship(
        back_populates="conference", cascade="all, delete-orphan", order_by="Milestone.date"
    )
    submissions: Mapped[list[Submission]] = relationship(back_populates="conference")

    # Series-level facts, read through the edition so feeds and API output keep
    # their shape ("venue" is the series acronym).
    @property
    def venue(self) -> str:
        return self.series.acronym if self.series else ""

    @property
    def name(self) -> str:
        return self.series.name if self.series else ""

    @property
    def emphasis(self) -> str:
        return self.series.emphasis if self.series else ""

    @property
    def fit(self) -> str:
        return self.series.fit if self.series else ""

    @property
    def label(self) -> str:
        return f"{self.venue} {self.year}"


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (Index("ix_milestones_conf_date", "conference_id", "date"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conference_id: Mapped[str] = mapped_column(
        ForeignKey("conferences.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    track: Mapped[str] = mapped_column(String(250), default="")
    milestone: Mapped[str] = mapped_column(String(250), default="")
    time_basis: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    conference: Mapped[Conference] = relationship(back_populates="milestones")

    @property
    def natural_key(self) -> str:
        return "|".join([self.conference.label, self.date.isoformat(), self.milestone, self.track])


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conference_id: Mapped[str | None] = mapped_column(
        ForeignKey("conferences.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(400))
    track: Mapped[str] = mapped_column(String(200), default="")
    lead: Mapped[str] = mapped_column(String(250), default="")
    coauthors: Mapped[str] = mapped_column(String(600), default="")
    status: Mapped[str] = mapped_column(String(60), default="Idea", index=True)
    project: Mapped[str] = mapped_column(String(120), default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    links: Mapped[str] = mapped_column(Text, default="")  # newline-separated

    created_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    conference: Mapped[Conference | None] = relationship(back_populates="submissions")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    updated_by: Mapped[User | None] = relationship(foreign_keys=[updated_by_id])

    @property
    def link_list(self) -> list[str]:
        return [x for x in (self.links or "").splitlines() if x.strip()]


class Star(Base):
    """A user's private star on a series (every edition) or an individual milestone."""

    __tablename__ = "stars"
    __table_args__ = (UniqueConstraint("user_id", "kind", "target_id", name="uq_star_user_target"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "series" | "milestone"
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship(back_populates="stars")


class ApiToken(Base):
    """A personal API token: acts as its owner, shown once, stored only as a hash."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="api_tokens_user_id_fkey"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16))  # the first characters, to recognise it
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()


class Change(Base):
    """One write to a series, edition or milestone, with what it was before and after.

    `before` is None for a create and `after` None for a delete. The snapshot of a
    deleted edition carries its milestones and the ideas that pointed at it, so
    undoing the delete brings them back.
    """

    __tablename__ = "changes"
    __table_args__ = (Index("ix_changes_entity", "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="changes_actor_id_fkey"), nullable=True
    )
    actor_name: Mapped[str] = mapped_column(String(255), default="")
    via: Mapped[str] = mapped_column(String(16), default="web")  # "web" | "token"
    token_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_tokens.id", ondelete="SET NULL", name="changes_token_id_fkey"),
        nullable=True,
    )
    token_name: Mapped[str] = mapped_column(String(120), default="")
    entity: Mapped[str] = mapped_column(String(16))  # "series" | "edition" | "milestone"
    entity_id: Mapped[str] = mapped_column(String(32))
    series_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(16))  # "create" | "update" | "delete"
    label: Mapped[str] = mapped_column(String(600), default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(1000), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    undo_of: Mapped[int | None] = mapped_column(
        ForeignKey("changes.id", ondelete="SET NULL", name="changes_undo_of_fkey"), nullable=True
    )
    undone_by: Mapped[int | None] = mapped_column(
        ForeignKey("changes.id", ondelete="SET NULL", name="changes_undone_by_fkey"),
        nullable=True,
    )
