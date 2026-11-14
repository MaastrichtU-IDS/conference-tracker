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

    client.post("/api/stars", json={"kind": "conference", "target_id": conf["id"]})
    client.post("/api/stars", json={"kind": "milestone", "target_id": ms["id"]})
    stars = client.get("/api/stars").json()
    assert conf["id"] in stars["conference"] and ms["id"] in stars["milestone"]

    # Starring twice must not create a duplicate.
    client.post("/api/stars", json={"kind": "conference", "target_id": conf["id"]})
    assert len(client.get("/api/stars").json()["conference"]) == 1

    feed_url = client.get("/api/me").json()["feed_url"]
    token = feed_url.rsplit("/", 1)[-1].removesuffix(".ics")
    res = client.get(f"/feeds/{token}.ics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/calendar")

    cal = Calendar.from_ical(res.text)
    events = list(cal.walk("vevent"))
    summaries = [str(e.get("SUMMARY")) for e in events]
    assert any(s.startswith("ESWC — European Semantic Web") for s in summaries)
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


def test_duplicate_venue_is_rejected(client):
    assert client.post("/api/conferences", json={"venue": "ESWC"}).status_code == 409


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
