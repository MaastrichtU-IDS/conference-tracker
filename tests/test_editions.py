"""Which edition of a series is "the next one" — the rule behind searching for ESWC."""

import datetime as dt
from types import SimpleNamespace as NS

import pytest

from app.editions import aoe_today, current_edition, has_open_deadline, milestone_kind

D = dt.date


def ms(date, milestone, track="Main track", end=None, time_basis=""):
    return NS(date=date, end_date=end, milestone=milestone, track=track, time_basis=time_basis)


def ed(year, abstract=None, paper=None, milestones=()):
    return NS(year=year, abstract_date=abstract, paper_date=paper, milestones=list(milestones))


# --- milestone kinds (mirrors kindOf() in app/static/app.js) ---------------------


@pytest.mark.parametrize(
    "milestone,track,kind",
    [
        ("Abstract submission", "Main track", "abstract"),
        ("Full paper submission", "Research track", "paper"),
        ("Submission deadline", "Posters", "paper"),
        ("Tutorial proposals due", "Tutorials", "proposal"),
        ("Author notification", "Main track", "notification"),
        ("Camera-ready due", "Main track", "final"),
        ("Submission site opens", "Main track", "admin"),
        ("Rebuttal period", "Main track", "review"),
    ],
)
def test_milestone_kind(milestone, track, kind):
    assert milestone_kind(ms(D(2027, 1, 1), milestone, track)) == kind


def test_event_dates_are_events():
    assert milestone_kind(ms(D(2027, 6, 1), "Conference", "Event")) == "event"


# --- open deadlines ------------------------------------------------------------


def test_headline_paper_date_ahead_is_open():
    assert has_open_deadline(ed(2027, paper=D(2026, 12, 10)), today=D(2026, 12, 10))


def test_deadline_passed_is_not_open():
    assert not has_open_deadline(ed(2027, paper=D(2026, 12, 10)), today=D(2026, 12, 11))


def test_a_later_proposal_milestone_keeps_an_edition_open():
    e = ed(
        2027,
        paper=D(2026, 12, 10),
        milestones=[ms(D(2027, 2, 1), "Workshop proposals due", "Workshops")],
    )
    assert has_open_deadline(e, today=D(2027, 1, 15))


def test_notifications_and_camera_ready_do_not_keep_it_open():
    e = ed(
        2027,
        paper=D(2026, 12, 10),
        milestones=[
            ms(D(2027, 2, 1), "Author notification"),
            ms(D(2027, 3, 1), "Camera-ready due"),
        ],
    )
    assert not has_open_deadline(e, today=D(2027, 1, 15))


def test_a_ranged_milestone_is_open_until_its_end():
    e = ed(2027, milestones=[ms(D(2027, 1, 1), "Paper submission", end=D(2027, 1, 20))])
    assert has_open_deadline(e, today=D(2027, 1, 15))


# --- the next edition ----------------------------------------------------------


def test_next_edition_is_the_earliest_open_one():
    e27, e28 = ed(2027, paper=D(2026, 12, 10)), ed(2028, paper=D(2027, 12, 10))
    assert current_edition([e28, e27], today=D(2026, 11, 1)) == (e27, True)


def test_switches_to_the_next_year_once_deadlines_pass():
    e27, e28 = ed(2027, paper=D(2026, 12, 10)), ed(2028, paper=D(2027, 12, 10))
    assert current_edition([e27, e28], today=D(2027, 1, 5)) == (e28, True)


def test_announced_without_dates_falls_back_to_latest_not_open():
    # 2027 closed, 2028 exists but has no dates yet: show 2028, flagged not open.
    e27, e28 = ed(2027, paper=D(2026, 12, 10)), ed(2028)
    assert current_edition([e27, e28], today=D(2027, 1, 5)) == (e28, False)


def test_nothing_announced_shows_the_latest_edition_not_open():
    e27 = ed(2027, paper=D(2026, 12, 10))
    assert current_edition([e27], today=D(2027, 1, 5)) == (e27, False)


def test_no_editions():
    assert current_edition([], today=D(2027, 1, 5)) == (None, False)


# --- AoE ------------------------------------------------------------------------


def test_aoe_today_lags_utc_by_twelve_hours():
    assert aoe_today(dt.datetime(2026, 12, 11, 11, 0, tzinfo=dt.UTC)) == D(2026, 12, 10)
    assert aoe_today(dt.datetime(2026, 12, 11, 12, 0, tzinfo=dt.UTC)) == D(2026, 12, 11)
