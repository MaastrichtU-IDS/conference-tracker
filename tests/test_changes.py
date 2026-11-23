"""The change log: every write to a series, edition or milestone is recorded with
its old and new values, and any recorded change can be undone while nothing has
touched that record since."""


def _changes(client, series_id, **params):
    res = client.get("/api/changes", params={"series": series_id, **params})
    assert res.status_code == 200, res.text
    return res.json()


def _ms(client, edition_id, **fields):
    body = {"date": "2098-11-01", "milestone": "Abstract submission", "track": "Research"}
    res = client.post(f"/api/conferences/{edition_id}/milestones", json={**body, **fields})
    assert res.status_code == 201, res.text
    return res.json()


def test_every_write_is_logged_newest_first(client, scratch):
    s, e = scratch
    m = _ms(client, e["id"])
    moved = {**m, "date": "2098-11-15"}
    src = "https://scratch.example/cfp"
    assert client.put(f"/api/milestones/{m['id']}", json=moved, params={"source": src}).is_success
    assert client.delete(f"/api/milestones/{m['id']}").status_code == 204

    log = _changes(client, s["id"])
    assert [(c["entity"], c["action"]) for c in log] == [
        ("milestone", "delete"),
        ("milestone", "update"),
        ("milestone", "create"),
        ("edition", "create"),
        ("series", "create"),
    ]
    delete, update, create, ed_create, _ = log
    assert update["label"] == "SCRATCH 2099 · Abstract submission (Research)"
    assert update["fields"] == ["date"]
    assert (update["before"]["date"], update["after"]["date"]) == ("2098-11-01", "2098-11-15")
    assert update["source"] == src
    assert update["actor"] == "Dev User" and update["via"] == "web"
    assert create["before"] is None and delete["after"] is None
    assert ed_create["label"] == "SCRATCH 2099"
    assert all(c["series_id"] == s["id"] for c in log)


def test_saving_without_changes_is_not_logged(client, scratch):
    s, e = scratch
    before = len(_changes(client, s["id"]))
    body = {k: e[k] for k in ("year", "paper_date", "location", "notes")}
    assert client.put(f"/api/conferences/{e['id']}", json=body).is_success
    assert len(_changes(client, s["id"])) == before


def test_undo_an_update_restores_the_old_value(client, scratch):
    s, e = scratch
    res = client.put(f"/api/conferences/{e['id']}", json={"year": 2099, "paper_date": "2098-12-20"})
    assert res.is_success
    change = _changes(client, s["id"])[0]

    undo = client.post(f"/api/changes/{change['id']}/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["undo_of"] == change["id"]
    ed = client.get(f"/api/series/{s['id']}").json()["editions"][0]
    assert ed["paper_date"] == "2098-12-01"

    log = _changes(client, s["id"])
    assert log[1]["id"] == change["id"] and log[1]["undone_by"] == log[0]["id"]
    # A change is undone once; undoing the undo is how to redo.
    assert client.post(f"/api/changes/{change['id']}/undo").status_code == 409
    assert client.post(f"/api/changes/{log[0]['id']}/undo").status_code == 200
    ed = client.get(f"/api/series/{s['id']}").json()["editions"][0]
    assert ed["paper_date"] == "2098-12-20"


def test_undo_refuses_to_overwrite_a_later_change(client, scratch):
    s, e = scratch
    client.put(f"/api/conferences/{e['id']}", json={"year": 2099, "paper_date": "2098-12-20"})
    first = _changes(client, s["id"])[0]
    client.put(f"/api/conferences/{e['id']}", json={"year": 2099, "paper_date": "2098-12-27"})
    second = _changes(client, s["id"])[0]

    res = client.post(f"/api/changes/{first['id']}/undo")
    assert res.status_code == 409 and "changed since" in res.json()["detail"]
    # Undone newest first, both apply.
    assert client.post(f"/api/changes/{second['id']}/undo").is_success
    assert client.post(f"/api/changes/{first['id']}/undo").is_success
    ed = client.get(f"/api/series/{s['id']}").json()["editions"][0]
    assert ed["paper_date"] == "2098-12-01"


def test_undo_a_create_removes_it(client, scratch):
    s, e = scratch
    m = _ms(client, e["id"])
    create = _changes(client, s["id"])[0]
    assert client.post(f"/api/changes/{create['id']}/undo").is_success
    ed = client.get(f"/api/series/{s['id']}").json()["editions"][0]
    assert all(x["id"] != m["id"] for x in ed["milestones"])


def test_undo_a_new_edition_is_refused_once_it_has_milestones(client, scratch):
    s, _ = scratch
    e = client.post(f"/api/series/{s['id']}/editions", json={"year": 2100}).json()
    create = _changes(client, s["id"])[0]
    m = _ms(client, e["id"])
    res = client.post(f"/api/changes/{create['id']}/undo")
    assert res.status_code == 409 and "milestones" in res.json()["detail"]
    client.delete(f"/api/milestones/{m['id']}")
    assert client.post(f"/api/changes/{create['id']}/undo").is_success


def test_undo_an_edition_delete_brings_back_its_milestones_and_ideas(client, scratch):
    s, e = scratch
    m = _ms(client, e["id"])
    sub = client.post("/api/submissions", json={"title": "Idea", "conference_id": e["id"]}).json()
    try:
        assert client.delete(f"/api/conferences/{e['id']}").status_code == 204
        subs = {x["id"]: x for x in client.get("/api/submissions").json()}
        assert subs[sub["id"]]["conference_id"] is None

        delete = _changes(client, s["id"])[0]
        assert (delete["entity"], delete["action"]) == ("edition", "delete")
        assert client.post(f"/api/changes/{delete['id']}/undo").is_success

        (ed,) = client.get(f"/api/series/{s['id']}").json()["editions"]
        assert ed["id"] == e["id"] and ed["paper_date"] == "2098-12-01"
        assert [x["id"] for x in ed["milestones"]] == [m["id"]]
        subs = {x["id"]: x for x in client.get("/api/submissions").json()}
        assert subs[sub["id"]]["conference_id"] == e["id"]
    finally:
        client.delete(f"/api/submissions/{sub['id']}")


def test_undo_will_not_restore_into_a_taken_year(client, scratch):
    s, e = scratch
    client.delete(f"/api/conferences/{e['id']}")
    delete = _changes(client, s["id"])[0]
    client.post(f"/api/series/{s['id']}/editions", json={"year": 2099})
    res = client.post(f"/api/changes/{delete['id']}/undo")
    assert res.status_code == 409


def test_undo_will_not_restore_a_taken_acronym(client, scratch):
    s, _ = scratch
    client.put(f"/api/series/{s['id']}", json={"acronym": "SCRATCH2"})
    rename = _changes(client, s["id"])[0]
    other = client.post("/api/series", json={"acronym": "SCRATCH"}).json()
    try:
        assert client.post(f"/api/changes/{rename['id']}/undo").status_code == 409
    finally:
        client.delete(f"/api/series/{other['id']}")


def test_the_log_can_be_paged(client, scratch):
    s, _ = scratch
    everything = _changes(client, s["id"])
    page = _changes(client, s["id"], limit=1)
    assert page == everything[:1]
    rest = _changes(client, s["id"], before=page[0]["id"])
    assert rest == everything[1:]


def test_series_filter_accepts_an_acronym(client, scratch):
    s, _ = scratch
    assert _changes(client, "scratch") == _changes(client, s["id"])


def test_unknown_change_is_404(client):
    assert client.post("/api/changes/999999/undo").status_code == 404


def test_the_change_log_is_for_admins(auth_on):
    assert auth_on.get("/api/changes").status_code == 401
    assert auth_on.post("/api/changes/1/undo").status_code == 401
