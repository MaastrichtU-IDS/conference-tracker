"""Alembic environment. Run through app.migrate.migrate(), which passes a connection.

render_as_batch lets the same revisions run on SQLite (tests, development),
where ALTER TABLE cannot drop columns or add constraints.
"""

from __future__ import annotations

from alembic import context


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=None, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:  # `alembic` CLI with -x / an ini file: use the app's engine
    from app.db import engine

    with engine.begin() as conn:
        _run(conn)
