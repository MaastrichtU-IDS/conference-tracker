"""Baseline: the schema v0.1.x created with metadata.create_all.

Names match what create_all produced in production (checked against
pg_indexes / pg_constraint on 2026-11-23), so a database created before
migrations existed is stamped at this revision and upgraded from here.

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("feed_token", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
    )
    op.create_index("ix_users_subject", "users", ["subject"], unique=True)
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_feed_token", "users", ["feed_token"], unique=True)

    op.create_table(
        "conferences",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("venue", sa.String(80), nullable=False),
        sa.Column("name", sa.String(400), nullable=False),
        sa.Column("location", sa.String(200), nullable=False),
        sa.Column("site_key", sa.String(120), nullable=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("status", sa.String(200), nullable=False),
        sa.Column("emphasis", sa.Text(), nullable=False),
        sa.Column("fit", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("abstract_date", sa.Date(), nullable=True),
        sa.Column("paper_date", sa.Date(), nullable=True),
        sa.Column("notify_date", sa.Date(), nullable=True),
        sa.Column("camera_date", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_conferences_venue", "conferences", ["venue"], unique=True)

    op.create_table(
        "milestones",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "conference_id",
            sa.String(32),
            sa.ForeignKey(
                "conferences.id", ondelete="CASCADE", name="milestones_conference_id_fkey"
            ),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("track", sa.String(250), nullable=False),
        sa.Column("milestone", sa.String(250), nullable=False),
        sa.Column("time_basis", sa.String(120), nullable=False),
        sa.Column("status", sa.String(200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_milestones_conference_id", "milestones", ["conference_id"])
    op.create_index("ix_milestones_date", "milestones", ["date"])
    op.create_index("ix_milestones_conf_date", "milestones", ["conference_id", "date"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "conference_id",
            sa.String(32),
            sa.ForeignKey(
                "conferences.id", ondelete="SET NULL", name="submissions_conference_id_fkey"
            ),
            nullable=True,
        ),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("track", sa.String(200), nullable=False),
        sa.Column("lead", sa.String(250), nullable=False),
        sa.Column("coauthors", sa.String(600), nullable=False),
        sa.Column("status", sa.String(60), nullable=False),
        sa.Column("project", sa.String(120), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("links", sa.Text(), nullable=False),
        sa.Column(
            "created_by_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="submissions_created_by_id_fkey"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="submissions_updated_by_id_fkey"),
            nullable=True,
        ),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_submissions_conference_id", "submissions", ["conference_id"])
    op.create_index("ix_submissions_status", "submissions", ["status"])

    op.create_table(
        "stars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="stars_user_id_fkey"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("user_id", "kind", "target_id", name="uq_star_user_target"),
    )
    op.create_index("ix_stars_user_id", "stars", ["user_id"])
    op.create_index("ix_stars_target_id", "stars", ["target_id"])


def downgrade() -> None:
    for table in ("stars", "submissions", "milestones", "conferences", "users"):
        op.drop_table(table)
