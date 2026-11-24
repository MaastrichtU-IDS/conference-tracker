"""Personal API tokens and the change log.

- `api_tokens`: a token per purpose (e.g. the monthly AI routine), acting as its
  owner. Only a SHA-256 hash is stored; the token itself is shown once.
- `changes`: every write to a series, edition or milestone, with the values
  before and after, who made it, through what (web or token), and the source it
  was taken from. Undo works from these rows.

Downgrade is not supported: restore a backup instead.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="api_tokens_user_id_fkey"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)

    op.create_table(
        "changes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="changes_actor_id_fkey"),
            nullable=True,
        ),
        sa.Column("actor_name", sa.String(255), nullable=False),
        sa.Column("via", sa.String(16), nullable=False),
        sa.Column(
            "token_id",
            sa.String(32),
            sa.ForeignKey("api_tokens.id", ondelete="SET NULL", name="changes_token_id_fkey"),
            nullable=True,
        ),
        sa.Column("token_name", sa.String(120), nullable=False),
        sa.Column("entity", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.String(32), nullable=False),
        sa.Column("series_id", sa.String(32), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("label", sa.String(600), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(1000), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "undo_of",
            sa.Integer(),
            sa.ForeignKey("changes.id", ondelete="SET NULL", name="changes_undo_of_fkey"),
            nullable=True,
        ),
        sa.Column(
            "undone_by",
            sa.Integer(),
            sa.ForeignKey("changes.id", ondelete="SET NULL", name="changes_undone_by_fkey"),
            nullable=True,
        ),
    )
    op.create_index("ix_changes_entity", "changes", ["entity", "entity_id"])
    op.create_index("ix_changes_series_id", "changes", ["series_id"])


def downgrade() -> None:
    raise NotImplementedError("Restore a backup instead")
