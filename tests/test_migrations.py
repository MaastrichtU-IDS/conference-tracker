"""Schema migrations, on SQLite always and on PostgreSQL when PG_TEST_URL is set.

docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=pg postgres:16.6-alpine
export PG_TEST_URL=postgresql+psycopg://postgres:pg@127.0.0.1:55432/postgres
pytest tests/test_migrations.py
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.migrate import migrate
from app.models import Base

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)

BACKENDS = ["sqlite"] + (["postgresql"] if os.getenv("PG_TEST_URL") else [])


@pytest.fixture(params=BACKENDS)
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    else:
        eng = create_engine(os.environ["PG_TEST_URL"])
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    yield eng
    eng.dispose()


def _legacy_database(engine) -> None:
    """A v0.1.x database: the 0001 schema, no alembic_version, some data."""
    migrate(engine, "0001")
    with engine.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))
        c.execute(
            text(
                "INSERT INTO users (id, subject, email, name, is_admin, feed_token, created_at,"
                " last_seen_at) VALUES ('u1', 'sub-1', 'a@x.nl', 'A', :f, 'tok', :now, :now)"
            ),
            {"f": False, "now": NOW},
        )
        conf = (
            "INSERT INTO conferences (id, venue, name, location, site_key, url, status, emphasis,"
            " fit, notes, source, start_date, end_date, paper_date, updated_at) VALUES"
            " (:id, :venue, :name, '', NULL, '', '', :emph, :fit, :notes, '', :start, :end,"
            " :paper, :now)"
        )
        c.execute(
            text(conf),
            {
                "id": "c-eswc",
                "venue": "ESWC",
                "name": "Extended Semantic Web Conference",
                "emph": "semantic web",
                "fit": "our core venue",
                "notes": "edition note",
                "start": dt.date(2027, 6, 1),
                "end": dt.date(2027, 6, 5),
                "paper": dt.date(2026, 12, 10),
                "now": NOW,
            },
        )
        c.execute(
            text(conf),
            {
                "id": "c-nodates",
                "venue": "NODATES",
                "name": "No Dates Yet",
                "emph": "",
                "fit": "",
                "notes": "",
                "start": None,
                "end": None,
                "paper": None,
                "now": NOW,
            },
        )
        c.execute(
            text(conf),
            {
                "id": "c-endonly",
                "venue": "ENDONLY",
                "name": "",
                "emph": "",
                "fit": "",
                "notes": "",
                "start": None,
                "end": dt.date(2026, 9, 3),
                "paper": None,
                "now": NOW,
            },
        )
        c.execute(
            text(
                "INSERT INTO milestones (id, conference_id, date, track, milestone, time_basis,"
                " status, notes, updated_at) VALUES ('m1', 'c-eswc', :d, 'Research', 'Paper"
                " submission', '', '', '', :now)"
            ),
            {"d": dt.date(2026, 12, 10), "now": NOW},
        )
        c.execute(
            text(
                "INSERT INTO submissions (id, conference_id, title, track, lead, coauthors, status,"
                " project, abstract, notes, links, created_at, updated_at) VALUES ('s1', 'c-eswc',"
                " 'Idea', '', '', '', 'Idea', '', '', '', '', :now, :now)"
            ),
            {"now": NOW},
        )
        for kind, target in (("conference", "c-eswc"), ("milestone", "m1"), ("conference", "gone")):
            c.execute(
                text(
                    "INSERT INTO stars (user_id, kind, target_id, created_at)"
                    " VALUES ('u1', :k, :t, :now)"
                ),
                {"k": kind, "t": target, "now": NOW},
            )


def test_fresh_database_reaches_head_and_matches_the_models(engine):
    migrate(engine)
    with engine.connect() as c:
        diffs = compare_metadata(MigrationContext.configure(c), Base.metadata)
        assert diffs == [], diffs
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


def test_legacy_database_is_stamped_and_converted(engine):
    _legacy_database(engine)
    migrate(engine)
    with engine.connect() as c:
        series = {
            r.acronym: r
            for r in c.execute(text("SELECT id, acronym, name, emphasis, fit FROM series"))
        }
        assert set(series) == {"ESWC", "NODATES", "ENDONLY"}
        eswc = series["ESWC"]
        assert (eswc.name, eswc.emphasis, eswc.fit) == (
            "Extended Semantic Web Conference",
            "semantic web",
            "our core venue",
        )

        eds = {
            r.id: r for r in c.execute(text("SELECT id, series_id, year, notes FROM conferences"))
        }
        # ids are kept, so milestones, submissions and calendar UIDs still point here
        assert set(eds) == {"c-eswc", "c-nodates", "c-endonly"}
        assert eds["c-eswc"].series_id == eswc.id and eds["c-eswc"].year == 2027
        assert eds["c-eswc"].notes == "edition note"
        assert eds["c-nodates"].year == 2027  # no dates: the 2027 cycle
        assert eds["c-endonly"].year == 2026  # falls back to the end date

        assert c.execute(text("SELECT conference_id FROM milestones")).scalar() == "c-eswc"
        assert c.execute(text("SELECT conference_id FROM submissions")).scalar() == "c-eswc"

        stars = set(c.execute(text("SELECT kind, target_id FROM stars")).all())
        assert stars == {("series", eswc.id), ("milestone", "m1")}  # orphan star dropped

        cols = {col["name"] for col in inspect(c).get_columns("conferences")}
        assert not cols & {"venue", "name", "emphasis", "fit"}


def test_migrate_twice_is_a_no_op(engine):
    _legacy_database(engine)
    migrate(engine)
    migrate(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM series")).scalar() == 3


def test_two_editions_of_one_series_must_differ_in_year(engine):
    migrate(engine)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO series (id, acronym, name, homepage, emphasis, fit, notes, updated_at)"
                " VALUES ('s', 'X', '', '', '', '', '', :now)"
            ),
            {"now": NOW},
        )
    ed = (
        "INSERT INTO conferences (id, series_id, year, location, url, status, notes, source,"
        " updated_at) VALUES (:id, 's', 2027, '', '', '', '', '', :now)"
    )
    with engine.begin() as c:
        c.execute(text(ed), {"id": "e1", "now": NOW})
    with pytest.raises(Exception), engine.begin() as c:  # noqa: B017 - driver-specific error
        c.execute(text(ed), {"id": "e2", "now": NOW})
