"""SQLAlchemy models.

Conferences and milestones are reference data, editable by admins in the app.
Submissions are shared across everyone. Stars are private to each user.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid

from sqlalchemy import (
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


class Conference(Base):
    __tablename__ = "conferences"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    venue: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(400), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    site_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(200), default="")
    emphasis: Mapped[str] = mapped_column(Text, default="")
    fit: Mapped[str] = mapped_column(Text, default="")
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

    milestones: Mapped[list[Milestone]] = relationship(
        back_populates="conference", cascade="all, delete-orphan", order_by="Milestone.date"
    )
    submissions: Mapped[list[Submission]] = relationship(back_populates="conference")


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
        return "|".join([self.conference.venue, self.date.isoformat(), self.milestone, self.track])


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
    """A user's private star on a conference or an individual milestone."""

    __tablename__ = "stars"
    __table_args__ = (UniqueConstraint("user_id", "kind", "target_id", name="uq_star_user_target"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "conference" | "milestone"
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship(back_populates="stars")
