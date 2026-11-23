"""Series and editions: ESWC is a series, ESWC 2027 one of its editions.

- New table `series` takes what lasts across years: acronym (the old `venue`),
  full name, focus (`emphasis`) and the group's `fit` notes; `homepage` and
  `notes` start empty.
- Each existing `conferences` row becomes an edition of its series, keeping its
  id, so milestones, submissions and calendar UIDs are untouched. Its `year` is
  the year of its start date, else its end date, else 2027: every row before
  this migration belonged to the 2027 cycle.
- Stars on a whole conference become stars on its series, so following ESWC
  carries over to ESWC 2028.

Downgrade is not supported: restore the pre-migration backup instead.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

LEGACY_YEAR = 2027


def _year(start, end) -> int:
    for value in (start, end):
        if value is None:
            continue
        if isinstance(value, str):  # SQLite returns dates as text through sa.text()
            return int(value[:4])
        return value.year
    return LEGACY_YEAR


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("acronym", sa.String(80), nullable=False),
        sa.Column("name", sa.String(400), nullable=False),
        sa.Column("homepage", sa.String(500), nullable=False),
        sa.Column("emphasis", sa.Text(), nullable=False),
        sa.Column("fit", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_series_acronym", "series", ["acronym"], unique=True)

    with op.batch_alter_table("conferences") as b:
        b.add_column(sa.Column("series_id", sa.String(32), nullable=True))
        b.add_column(sa.Column("year", sa.Integer(), nullable=True))

    bind = op.get_bind()
    now = dt.datetime.now(dt.UTC)
    rows = bind.execute(
        sa.text("SELECT id, venue, name, emphasis, fit, start_date, end_date FROM conferences")
    ).mappings()
    for r in list(rows):
        series_id = uuid.uuid4().hex
        bind.execute(
            sa.text(
                "INSERT INTO series (id, acronym, name, homepage, emphasis, fit, notes, updated_at)"
                " VALUES (:id, :acronym, :name, '', :emphasis, :fit, '', :now)"
            ),
            {
                "id": series_id,
                "acronym": r["venue"],
                "name": r["name"] or "",
                "emphasis": r["emphasis"] or "",
                "fit": r["fit"] or "",
                "now": now,
            },
        )
        bind.execute(
            sa.text("UPDATE conferences SET series_id = :sid, year = :year WHERE id = :id"),
            {"sid": series_id, "year": _year(r["start_date"], r["end_date"]), "id": r["id"]},
        )
        bind.execute(
            sa.text(
                "UPDATE stars SET kind = 'series', target_id = :sid"
                " WHERE kind = 'conference' AND target_id = :id"
            ),
            {"sid": series_id, "id": r["id"]},
        )
    # Stars on conferences that no longer exist point at nothing.
    bind.execute(sa.text("DELETE FROM stars WHERE kind = 'conference'"))

    op.drop_index("ix_conferences_venue", table_name="conferences")
    with op.batch_alter_table("conferences") as b:
        b.alter_column("series_id", existing_type=sa.String(32), nullable=False)
        b.alter_column("year", existing_type=sa.Integer(), nullable=False)
        for column in ("venue", "name", "emphasis", "fit"):
            b.drop_column(column)
        b.create_foreign_key(
            "conferences_series_id_fkey", "series", ["series_id"], ["id"], ondelete="RESTRICT"
        )
        b.create_unique_constraint("uq_conference_series_year", ["series_id", "year"])
        b.create_index("ix_conferences_series_id", ["series_id"])


def downgrade() -> None:
    raise NotImplementedError("Restore the backup taken before 0002 instead.")
