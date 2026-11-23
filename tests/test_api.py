"""End-to-end API tests. Auth is disabled, so every call is the dev user (an admin)."""

import datetime as dt

from icalendar import Calendar


def test_seed_loaded(client):
    data = client.get("/api/bootstrap").json()
    assert len(data["conferences"]) == 29
    assert len(data["milestones"]) == 174
    venues = {c["venue"] for c in data["conferences"]}
    assert {"ESWC", "ISWC", "SWAT4HCLS", "MIE", "MedInfo", "AMIA", "BioHackathon"} <= venues


def test_seed_is_idempotent(client):
    """A second boot must not duplicate the dataset."""
    from app.db import SessionLocal
    from app.seed import seed_if_empty

    with SessionLocal() as db:
        assert seed_if_empty(db, "seed/conferences.json") == 0
    assert len(client.get("/api/bootstrap").json()["conferences"]) == 29


def test_headline_dates_survive_the_round_trip(client):
    confs = {c["venue"]: c for c in client.get("/api/bootstrap").json()["conferences"]}
    eswc = confs["ESWC"]
    assert eswc["paper_date"] == "2026-12-17"
    assert eswc["start_date"] == "2027-05-23"
    assert eswc["site_key"] == "Limassol, Cyprus"
    assert confs["BioHackathon"]["start_date"] is None


def test_submission_lifecycle(client):
    confs = {c["venue"]: c for c in client.get("/api/bootstrap").json()["conferences"]}
    body = {
        "title": "OntoExplorer",
        "conference_id": confs["ESWC"]["id"],
        "track": "Resources",
        "lead": "Michel Dumontier",
        "status": "Idea",
        "links": ["https://ontoexplorer.dev.k8s.semanticscience.org/"],
    }
    created = client.post("/api/submissions", json=body).json()
    assert created["venue"] == "ESWC"
    assert created["links"] == body["links"]
    assert created["created_by"]

    body["status"] = "Submitted"
    updated = client.put(f"/api/submissions/{created['id']}", json=body).json()
    assert updated["status"] == "Submitted"
    assert updated["id"] == created["id"]

    assert len(client.get("/api/submissions").json()) == 1
    assert client.delete(f"/api/submissions/{created['id']}").status_code == 204
    assert client.get("/api/submissions").json() == []


def test_stars_and_personal_feed(client):
    data = client.get("/api/bootstrap").json()
    conf = next(c for c in data["conferences"] if c["venue"] == "ESWC")
    ms = next(m for m in data["milestones"] if m["venue"] == "IJCAI")

    client.post("/api/stars", json={"kind": "series", "target_id": conf["series_id"]})
    client.post("/api/stars", json={"kind": "milestone", "target_id": ms["id"]})
    stars = client.get("/api/stars").json()
    assert conf["series_id"] in stars["series"] and ms["id"] in stars["milestone"]

    # Starring twice must not create a duplicate, and an old page starring the
    # edition ("conference") lands on the same series star.
    client.post("/api/stars", json={"kind": "series", "target_id": conf["series_id"]})
    client.post("/api/stars", json={"kind": "conference", "target_id": conf["id"]})
    assert client.get("/api/stars").json()["series"] == [conf["series_id"]]

    feed_url = client.get("/api/me").json()["feed_url"]
    token = feed_url.rsplit("/", 1)[-1].removesuffix(".ics")
    res = client.get(f"/feeds/{token}.ics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/calendar")

    cal = Calendar.from_ical(res.text)
    events = list(cal.walk("vevent"))
    summaries = [str(e.get("SUMMARY")) for e in events]
    assert any(s.startswith("ESWC 2027 — European Semantic Web") for s in summaries)
    assert any("IJCAI" in s for s in summaries)
    # Every entry is all-day, and UIDs are unique.
    assert all(isinstance(e.get("DTSTART").dt, dt.date) for e in events)
    uids = [str(e.get("UID")) for e in events]
    assert len(set(uids)) == len(uids)

    # Un-starring removes it again.
    client.delete("/api/stars", params={"kind": "milestone", "target_id": ms["id"]})
    assert ms["id"] not in client.get("/api/stars").json()["milestone"]


def test_rotating_the_token_invalidates_the_old_feed(client):
    old = client.get("/api/me").json()["feed_url"]
    old_token = old.rsplit("/", 1)[-1].removesuffix(".ics")
    new = client.post("/api/feed-token/rotate").json()["feed_url"]
    assert new != old
    assert client.get(f"/feeds/{old_token}.ics").status_code == 404
    assert client.get(f"/feeds/{new.rsplit('/', 1)[-1]}").status_code == 200


def test_public_feed_covers_everything(client):
    cal = Calendar.from_ical(client.get("/feeds/all.ics").text)
    events = list(cal.walk("vevent"))
    # 21 conferences carry event dates; all 174 milestones are dated.
    assert len(events) == 21 + 174
    assert str(cal.get("X-WR-CALNAME"))


def test_unknown_feed_token_is_404(client):
    assert client.get("/feeds/nope.ics").status_code == 404


def test_admin_can_edit_a_conference(client):
    conf = next(c for c in client.get("/api/bootstrap").json()["conferences"] if c["venue"] == "KR")
    conf["notes"] = "Deadlines confirmed against the official CFP."
    conf["paper_date"] = "2027-04-29"
    updated = client.put(f"/api/conferences/{conf['id']}", json=conf).json()
    assert updated["paper_date"] == "2027-04-29"
    assert "confirmed" in updated["notes"]


def test_admin_can_add_and_remove_a_milestone(client):
    conf = next(
        c for c in client.get("/api/bootstrap").json()["conferences"] if c["venue"] == "ISWC"
    )
    created = client.post(
        f"/api/conferences/{conf['id']}/milestones",
        json={
            "date": "2027-05-04",
            "milestone": "Full paper",
            "track": "Research",
            "time_basis": "23:59 AoE (UTC−12)",
        },
    ).json()
    assert created["venue"] == "ISWC"
    assert any(m["id"] == created["id"] for m in client.get("/api/bootstrap").json()["milestones"])
    assert client.delete(f"/api/milestones/{created['id']}").status_code == 204


def test_duplicate_series_is_rejected_whatever_the_case(client):
    assert client.post("/api/series", json={"acronym": "eswc"}).status_code == 409


def test_search_lands_on_the_series_and_its_next_edition(client):
    found = client.get("/api/series", params={"q": "eswc"}).json()
    assert found[0]["acronym"] == "ESWC"
    eswc = found[0]
    assert eswc["years"] == [2027] and eswc["current_year"] == 2027

    detail = client.get("/api/series/eswc").json()  # by acronym, any case
    assert detail["id"] == eswc["id"]
    (edition,) = detail["editions"]
    assert edition["id"] == eswc["current_edition_id"] and edition["label"] == "ESWC 2027"
    assert edition["milestones"] and all(m["label"] == "ESWC 2027" for m in edition["milestones"])
    assert client.get(f"/api/series/{eswc['id']}").json()["acronym"] == "ESWC"  # by id
    assert client.get("/api/series/NOPE").status_code == 404


def test_search_by_name_and_exact_acronym_first(client):
    names = [s["acronym"] for s in client.get("/api/series", params={"q": "semantic"}).json()]
    assert "ESWC" in names and "ISWC" in names
    assert client.get("/api/series", params={"q": "ISWC"}).json()[0]["acronym"] == "ISWC"


def test_series_and_editions_lifecycle(client):
    s = client.post(
        "/api/series", json={"acronym": "TESTCONF", "name": "Test Conference", "fit": "ours"}
    ).json()
    try:
        assert s["years"] == [] and s["current_edition_id"] is None and not s["open"]

        past = {"year": 2025, "paper_date": "2024-12-01", "location": "Old Town"}
        future = {"year": 2099, "paper_date": "2098-12-01", "location": "New Town"}
        e25 = client.post(f"/api/series/{s['id']}/editions", json=past).json()
        e99 = client.post(f"/api/series/{s['id']}/editions", json=future).json()
        assert e99["label"] == "TESTCONF 2099" and e99["fit"] == "ours"
        dup = client.post(f"/api/series/{s['id']}/editions", json={"year": 2025})
        assert dup.status_code == 409
        moved = client.put(f"/api/conferences/{e25['id']}", json={**past, "year": 2099})
        assert moved.status_code == 409  # cannot collide with an existing year

        # The open 2099 edition is the one to show, not the closed 2025 one.
        found = client.get("/api/series", params={"q": "testconf"}).json()[0]
        assert found["years"] == [2025, 2099]
        assert (found["current_edition_id"], found["open"]) == (e99["id"], True)

        renamed = client.put(
            f"/api/series/{s['id']}", json={"acronym": "TESTCONF", "name": "Renamed"}
        ).json()
        assert renamed["name"] == "Renamed"
        clash = client.put(f"/api/series/{s['id']}", json={"acronym": "eswc"})
        assert clash.status_code == 409

        # A series with editions cannot be deleted.
        assert client.delete(f"/api/series/{s['id']}").status_code == 409
        for e in (e25, e99):
            assert client.delete(f"/api/conferences/{e['id']}").status_code == 204
    finally:
        client.delete(f"/api/series/{s['id']}")
    assert client.get(f"/api/series/{s['id']}").status_code == 404


def test_bootstrap_lists_series_with_their_current_edition(client):
    data = client.get("/api/bootstrap").json()
    assert len(data["series"]) == 29
    eds = {c["id"] for c in data["conferences"]}
    assert all(s["current_edition_id"] in eds for s in data["series"])
    assert set(data["stars"]) == {"series", "milestone"}


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/api/healthz").json()["status"] == "ok"


def test_initdb_entrypoint_is_idempotent(tmp_path, monkeypatch):
    """The PreSync Job's entrypoint must be safe to re-run on every sync."""
    import importlib

    from app import initdb

    assert initdb.main() == 0
    assert initdb.main() == 0
    importlib.reload(initdb)
