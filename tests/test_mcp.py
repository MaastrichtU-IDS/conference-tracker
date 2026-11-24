"""The MCP server at /mcp: what an AI sees and may do.

Requests are raw JSON-RPC over Streamable HTTP (stateless, JSON responses), the
way a remote MCP client sends them.
"""

import itertools

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Conference, User

ACCEPT = {"Accept": "application/json, text/event-stream"}
_ids = itertools.count(1)


@pytest.fixture
def token(client):
    t = client.post("/api/tokens", json={"name": "mcp test"}).json()
    yield t["token"]
    client.delete(f"/api/tokens/{t['id']}")


def rpc(client, token, method, params=None, **headers):
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or {}}
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/mcp", json=body, headers={**ACCEPT, **auth, **headers})


def call(client, token, tool, /, **arguments):
    """A tool's result: (is_error, structured content or error text)."""
    res = rpc(client, token, "tools/call", {"name": tool, "arguments": arguments})
    assert res.status_code == 200, res.text
    result = res.json()["result"]
    if result.get("isError"):
        return True, result["content"][0]["text"]
    return False, result.get("structuredContent")


def ok(client, token, tool, /, **arguments):
    err, out = call(client, token, tool, **arguments)
    assert not err, out
    return out


SRC = "https://scratch.example/cfp"


# --- who gets in -----------------------------------------------------------------


def test_no_token_no_entry_even_in_dev_mode(client):
    res = rpc(client, None, "tools/list")
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert "API token" in res.json()["detail"]


@pytest.mark.parametrize("header", ["Bearer ct_bogus", "Basic Zm9vOmJhcg==", "Bearer"])
def test_bad_tokens_are_refused(client, header):
    res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**ACCEPT, "Authorization": header},
    )
    assert res.status_code == 401


def test_a_revoked_token_is_refused(client):
    t = client.post("/api/tokens", json={"name": "gone"}).json()
    assert rpc(client, t["token"], "tools/list").status_code == 200
    client.delete(f"/api/tokens/{t['id']}")
    assert rpc(client, t["token"], "tools/list").status_code == 401


def test_a_token_whose_owner_lost_admin_is_refused(client, token):
    with SessionLocal() as db:
        db.scalar(select(User).where(User.subject == "dev")).is_admin = False
        db.commit()
    try:
        assert rpc(client, token, "tools/list").status_code == 403
    finally:
        with SessionLocal() as db:
            db.scalar(select(User).where(User.subject == "dev")).is_admin = True
            db.commit()


def test_public_hostnames_are_served(client, token):
    """The SDK's localhost-only default would refuse the real site with a 421."""
    res = rpc(client, token, "tools/list", host="conference-tracker.k8s.semanticscience.org")
    assert res.status_code == 200


# --- what it offers ----------------------------------------------------------------


def test_the_tools_on_offer(client, token):
    tools = {t["name"]: t for t in rpc(client, token, "tools/list").json()["result"]["tools"]}
    assert set(tools) == {
        "tracker_search_series",
        "tracker_get_series",
        "tracker_list_changes",
        "tracker_add_series",
        "tracker_update_series",
        "tracker_add_edition",
        "tracker_update_edition",
        "tracker_add_milestone",
        "tracker_update_milestone",
        "tracker_delete_milestone",
        "tracker_undo_change",
    }
    for name in ("tracker_search_series", "tracker_get_series", "tracker_list_changes"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
    assert tools["tracker_delete_milestone"]["annotations"]["destructiveHint"] is True
    # Every add or update must cite its source.
    for name, t in tools.items():
        if name.startswith(("tracker_add_", "tracker_update_")):
            assert "source_url" in t["inputSchema"]["required"], name
    assert not tools["tracker_search_series"]["description"].startswith(" ")


def test_the_server_explains_the_model(client, token):
    res = rpc(
        client,
        token,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )
    assert res.status_code == 200, res.text
    assert "AoE" in res.json()["result"]["instructions"]


# --- reading -----------------------------------------------------------------------


def test_search_lands_on_the_series_and_filters_by_state(client, token):
    found = ok(client, token, "tracker_search_series", query="eswc")["result"]
    assert found[0]["acronym"] == "ESWC" and found[0]["current_year"] == 2027
    for state in ("open", "closed", "awaiting"):
        listed = ok(client, token, "tracker_search_series", state=state)["result"]
        assert all(s["state"] == state for s in listed)


def test_get_series_by_acronym(client, token):
    got = ok(client, token, "tracker_get_series", series="eswc")
    assert got["acronym"] == "ESWC" and got["editions"][0]["milestones"]
    err, text = call(client, token, "tracker_get_series", series="NOPE")
    assert err and "tracker_search_series" in text


# --- writing -----------------------------------------------------------------------


def test_add_update_and_undo_a_milestone(client, token, scratch):
    s, e = scratch
    added = ok(
        client,
        token,
        "tracker_add_milestone",
        edition="SCRATCH 2099",
        date="2098-11-01",
        milestone="Full paper submission",
        track="Main track",
        time_basis="23:59 AoE (UTC−12)",
        source_url=SRC,
        note="from the CFP",
    )
    assert added["changed"] and added["change"]["action"] == "create"
    assert added["change"]["via"] == "token" and added["change"]["token"] == "mcp test"
    assert added["change"]["source"] == SRC and added["change"]["note"] == "from the CFP"

    # Partial: only the date moves; the other fields stay as they were.
    moved = ok(
        client,
        token,
        "tracker_update_milestone",
        milestone_id=added["id"],
        date="2098-11-15",
        source_url=SRC,
        note="extended",
    )
    assert moved["change"]["fields"] == ["date"]
    (ms,) = ok(client, token, "tracker_get_series", series="SCRATCH")["editions"][0]["milestones"]
    assert (ms["date"], ms["track"], ms["time_basis"]) == (
        "2098-11-15",
        "Main track",
        "23:59 AoE (UTC−12)",
    )

    undone = ok(client, token, "tracker_undo_change", change_id=moved["change"]["id"])
    assert undone["change"]["undo_of"] == moved["change"]["id"]
    (ms,) = ok(client, token, "tracker_get_series", series="SCRATCH")["editions"][0]["milestones"]
    assert ms["date"] == "2098-11-01"

    log = ok(client, token, "tracker_list_changes", series="SCRATCH", limit=3)["result"]
    assert [c["id"] for c in log] == [
        undone["change"]["id"],
        moved["change"]["id"],
        added["change"]["id"],
    ]


def test_an_unchanged_update_says_so(client, token, scratch):
    res = ok(
        client,
        token,
        "tracker_update_edition",
        edition="SCRATCH 2099",
        paper_date="2098-12-01",
        source_url=SRC,
    )
    assert res == {"changed": False, "id": scratch[1]["id"], "change": None}


def test_update_edition_dates_and_clear_one(client, token, scratch):
    _, e = scratch
    res = ok(
        client,
        token,
        "tracker_update_edition",
        edition=e["id"],
        abstract_date="2098-11-20",
        clear=["paper_date"],
        source_url=SRC,
    )
    assert sorted(res["change"]["fields"]) == ["abstract_date", "paper_date"]
    with SessionLocal() as db:
        conf = db.get(Conference, e["id"])
        assert conf.paper_date is None and str(conf.abstract_date) == "2098-11-20"
        assert conf.year == 2099


def test_a_new_venue_and_its_first_edition(client, token):
    try:
        ok(
            client,
            token,
            "tracker_add_series",
            acronym="NEWVENUE",
            name="A New Venue",
            homepage="https://newvenue.example/",
            source_url="https://newvenue.example/",
        )
        ed = ok(
            client,
            token,
            "tracker_add_edition",
            series="newvenue",
            year=2099,
            paper_date="2098-10-01",
            source_url="https://newvenue.example/2099/cfp",
        )
        got = ok(client, token, "tracker_get_series", series="NEWVENUE")
        assert got["state"] == "open" and got["editions"][0]["id"] == ed["id"]
    finally:
        detail = client.get("/api/series/NEWVENUE")
        if detail.status_code == 200:
            for x in detail.json()["editions"]:
                client.delete(f"/api/conferences/{x['id']}")
            client.delete(f"/api/series/{detail.json()['id']}")


# --- refusals reach the model as readable errors ------------------------------------


@pytest.mark.parametrize("source", [None, "", "eswc.org", "javascript:alert(1)"])
def test_writes_must_cite_a_web_source(client, token, scratch, source):
    args = {"edition": "SCRATCH 2099", "date": "2098-11-01", "milestone": "Notification"}
    if source is not None:
        args["source_url"] = source
    err, text = call(client, token, "tracker_add_milestone", **args)
    assert err and "source_url" in text


def test_a_clash_is_explained(client, token, scratch):
    err, text = call(
        client, token, "tracker_add_edition", series="SCRATCH", year=2099, source_url=SRC
    )
    assert err and "2099" in text


def test_a_stale_undo_is_explained(client, token, scratch):
    first = ok(
        client,
        token,
        "tracker_update_edition",
        edition="SCRATCH 2099",
        paper_date="2098-12-20",
        source_url=SRC,
    )
    ok(
        client,
        token,
        "tracker_update_edition",
        edition="SCRATCH 2099",
        paper_date="2098-12-27",
        source_url=SRC,
    )
    err, text = call(client, token, "tracker_undo_change", change_id=first["change"]["id"])
    assert err and "changed since" in text


def test_bad_links_are_explained(client, token, scratch):
    err, text = call(
        client,
        token,
        "tracker_update_series",
        series="SCRATCH",
        homepage="scratch.example",
        source_url=SRC,
    )
    assert err and "homepage" in text and "http" in text


def test_an_unknown_edition_is_explained(client, token):
    err, text = call(
        client,
        token,
        "tracker_add_milestone",
        edition="ESWC 1999",
        date="1998-11-01",
        milestone="Notification",
        source_url=SRC,
    )
    assert err and "ESWC 2027" in text


def test_deleting_a_milestone_needs_a_reason(client, token, scratch):
    m = ok(
        client,
        token,
        "tracker_add_milestone",
        edition="SCRATCH 2099",
        date="2098-11-01",
        milestone="Notification",
        source_url=SRC,
    )
    err, text = call(client, token, "tracker_delete_milestone", milestone_id=m["id"])
    assert err and "reason" in text
    gone = ok(
        client,
        token,
        "tracker_delete_milestone",
        milestone_id=m["id"],
        reason="duplicate of the main-track notification",
    )
    assert gone["change"]["action"] == "delete"
    assert gone["change"]["note"] == "duplicate of the main-track notification"
