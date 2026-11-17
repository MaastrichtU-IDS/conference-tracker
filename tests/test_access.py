"""What an anonymous visitor can and cannot read when sign-in is enabled.

Conferences and milestones are public deadlines (they are in /feeds/all.ics
anyway). Submissions are the group's unpublished work and who is writing it, so
they are for signed-in members only.
"""

from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


@contextmanager
def anonymous_with_auth_enabled():
    """A client with no session, against an app whose OIDC is configured."""
    settings = Settings()
    settings.oidc_issuer = "https://idp.example/realms/test"
    settings.oidc_client_id = "conference-tracker"
    settings.oidc_client_secret = "test-secret"
    settings.dev_auth = False
    assert settings.auth_enabled

    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as anon:
            yield anon
    finally:
        app.dependency_overrides.pop(get_settings, None)


def _make_submission(client) -> dict:
    confs = client.get("/api/bootstrap").json()["conferences"]
    conf = next(c for c in confs if c["venue"] == "ISWC")
    res = client.post(
        "/api/submissions",
        json={"title": "Unpublished work", "conference_id": conf["id"], "lead": "A. Member"},
    )
    assert res.status_code == 201
    return res.json()


def test_anonymous_cannot_list_submissions(client):
    sub = _make_submission(client)
    try:
        with anonymous_with_auth_enabled() as anon:
            assert anon.get("/api/submissions").status_code == 401
    finally:
        client.delete(f"/api/submissions/{sub['id']}")


def test_anonymous_bootstrap_has_deadlines_but_no_submissions(client):
    sub = _make_submission(client)
    try:
        with anonymous_with_auth_enabled() as anon:
            res = anon.get("/api/bootstrap")
            assert res.status_code == 200
            data = res.json()
            assert data["me"] == {**data["me"], "authenticated": False, "auth_enabled": True}
            assert len(data["conferences"]) == 29
            assert len(data["milestones"]) >= 174
            assert data["submissions"] == []
            assert "Unpublished work" not in res.text
    finally:
        client.delete(f"/api/submissions/{sub['id']}")


def test_anonymous_cannot_write(client):
    with anonymous_with_auth_enabled() as anon:
        assert anon.post("/api/submissions", json={"title": "x"}).status_code == 401
        star = {"kind": "conference", "target_id": "x"}
        assert anon.post("/api/stars", json=star).status_code == 401


def test_calendar_feeds_stay_open_without_sign_in(client):
    """Outlook and Google fetch these with no credentials."""
    with anonymous_with_auth_enabled() as anon:
        assert anon.get("/feeds/all.ics").status_code == 200
        assert anon.get("/api/me").status_code == 200


def test_signed_in_member_still_sees_submissions(client):
    sub = _make_submission(client)
    try:
        assert any(s["id"] == sub["id"] for s in client.get("/api/submissions").json())
        assert any(s["id"] == sub["id"] for s in client.get("/api/bootstrap").json()["submissions"])
    finally:
        client.delete(f"/api/submissions/{sub['id']}")
