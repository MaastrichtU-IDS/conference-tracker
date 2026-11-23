"""Bring the database schema to the latest revision (Alembic, app/migrations).

A database created before migrations existed (v0.1.x, metadata.create_all) has
the tables but no `alembic_version`: it is stamped at the baseline 0001, which
describes exactly that schema, and upgraded from there. Everything runs in one
transaction, so on PostgreSQL a failed migration leaves the database as it was.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

log = logging.getLogger(__name__)
SCRIPT_LOCATION = Path(__file__).parent / "migrations"
BASELINE = "0001"


def _config(connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    cfg.attributes["connection"] = connection
    return cfg


def migrate(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as conn:
        cfg = _config(conn)
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "conferences" in tables:
            log.info("pre-migration schema found; stamping baseline %s", BASELINE)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, revision)
    log.info("schema at %s", revision)
