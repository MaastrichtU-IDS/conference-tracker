"""Personal API tokens: how an AI (or a script) edits the tracker as its admin owner.

A token is shown once, stored only as a hash, expires, can be revoked, and acts
with its owner's rights. Tokens cannot manage tokens: a leaked one cannot mint
another or outlive its revocation.
"""

import datetime as dt
import hashlib

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ApiToken, User


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(client):
    res = client.post("/api/tokens", json={"name": "monthly routine", "expires_in_days": 30})
    assert res.status_code == 201, res.text
    body = res.json()
    yield body
    client.delete(f"/api/tokens/{body['id']}")


def test_a_new_token_is_shown_once_and_stored_hashed(client, token):
    assert token["token"].startswith("ct_") and len(token["token"]) > 40
    assert token["token"].startswith(token["prefix"])
    expires = dt.datetime.fromisoformat(token["expires_at"])
    assert dt.timedelta(days=29) < expires - dt.datetime.now(dt.UTC) <= dt.timedelta(days=30)

    listed = {t["id"]: t for t in client.get("/api/tokens").json()}
    assert "token" not in listed[token["id"]]
    assert listed[token["id"]]["owner"] == "Dev User"

    with SessionLocal() as db:
        row = db.get(ApiToken, token["id"])
        assert row.token_hash == hashlib.sha256(token["token"].encode()).hexdigest()


def test_a_token_acts_as_its_admin_owner(client, token, auth_on, scratch):
    s, e = scratch
    me = auth_on.get("/api/me", headers=_bearer(token["token"])).json()
    assert me["authenticated"] and me["is_admin"]

    res = auth_on.put(
        f"/api/conferences/{e['id']}",
        json={"year": 2099, "paper_date": "2098-12-08"},
        params={"source": "https://scratch.example/cfp", "note": "CFP extended by a week"},
        headers=_bearer(token["token"]),
    )
    assert res.status_code == 200, res.text

    change = client.get("/api/changes", params={"series": s["id"]}).json()[0]
    assert (change["via"], change["token"]) == ("token", "monthly routine")
    assert change["note"] == "CFP extended by a week"

    used = {t["id"]: t for t in client.get("/api/tokens").json()}[token["id"]]
    assert used["last_used_at"] is not None


def test_without_a_token_the_same_write_is_refused(auth_on, scratch):
    _, e = scratch
    assert auth_on.put(f"/api/conferences/{e['id']}", json={"year": 2099}).status_code == 401


@pytest.mark.parametrize("value", ["ct_not-a-real-token", "", "garbage"])
def test_a_bad_token_is_401_not_anonymous(auth_on, client, value):
    res = auth_on.get("/api/me", headers={"Authorization": f"Bearer {value}"})
    assert res.status_code == 401
    # Even in dev mode a bad token is refused rather than ignored.
    assert client.get("/api/me", headers={"Authorization": f"Bearer {value}"}).status_code == 401


def test_a_revoked_token_stops_working(client, auth_on):
    t = client.post("/api/tokens", json={"name": "short-lived"}).json()
    assert auth_on.get("/api/changes", headers=_bearer(t["token"])).status_code == 200
    assert client.delete(f"/api/tokens/{t['id']}").status_code == 204
    assert auth_on.get("/api/changes", headers=_bearer(t["token"])).status_code == 401
    listed = {x["id"]: x for x in client.get("/api/tokens").json()}
    assert listed[t["id"]]["revoked_at"] is not None


def test_an_expired_token_stops_working(client, token, auth_on):
    with SessionLocal() as db:
        db.get(ApiToken, token["id"]).expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        db.commit()
    assert auth_on.get("/api/me", headers=_bearer(token["token"])).status_code == 401


def test_tokens_cannot_manage_tokens_or_feeds(token, auth_on):
    h = _bearer(token["token"])
    assert auth_on.post("/api/tokens", json={"name": "child"}, headers=h).status_code == 403
    assert auth_on.get("/api/tokens", headers=h).status_code == 403
    assert auth_on.delete(f"/api/tokens/{token['id']}", headers=h).status_code == 403
    assert auth_on.post("/api/feed-token/rotate", headers=h).status_code == 403


def test_a_token_of_someone_no_longer_admin_cannot_write(client, token, auth_on, scratch):
    _, e = scratch
    with SessionLocal() as db:
        dev = db.scalar(select(User).where(User.subject == "dev"))
        dev.is_admin = False
        db.commit()
    try:
        res = auth_on.put(
            f"/api/conferences/{e['id']}", json={"year": 2099}, headers=_bearer(token["token"])
        )
        assert res.status_code == 403
    finally:
        with SessionLocal() as db:
            db.scalar(select(User).where(User.subject == "dev")).is_admin = True
            db.commit()


@pytest.mark.parametrize("days", [0, 366])
def test_lifetime_is_bounded(client, days):
    res = client.post("/api/tokens", json={"name": "x", "expires_in_days": days})
    assert res.status_code == 422


def test_token_endpoints_need_a_signed_in_admin(auth_on):
    assert auth_on.get("/api/tokens").status_code == 401
    assert auth_on.post("/api/tokens", json={"name": "x"}).status_code == 401
