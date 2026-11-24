import os
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TMP / 'test.db'}")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("BASE_URL", "http://testserver")
os.environ.setdefault(
    "SEED_FILE", str(Path(__file__).resolve().parents[1] / "seed" / "conferences.json")
)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_on():
    """A fresh client (no session cookie) that sees the app with OIDC configured.

    Only this client's requests get the sign-in-enabled settings, so the dev-mode
    `client` fixture keeps working alongside it. Pass
    headers={"Authorization": "Bearer …"} to act with an API token.
    """
    from fastapi import Request

    from app.config import Settings, get_settings

    settings = Settings()
    settings.oidc_issuer = "https://idp.example/realms/test"
    settings.oidc_client_id = "conference-tracker"
    settings.oidc_client_secret = "test-secret"
    settings.dev_auth = False
    assert settings.auth_enabled

    def pick(request: Request) -> Settings:
        return settings if request.headers.get("x-test-auth") == "on" else get_settings()

    app.dependency_overrides[get_settings] = pick
    try:
        with TestClient(app, headers={"X-Test-Auth": "on"}) as anon:
            yield anon
    finally:
        app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def scratch(client):
    """A throwaway series (SCRATCH) with a 2099 edition; removed afterwards."""
    s = client.post("/api/series", json={"acronym": "SCRATCH", "name": "Scratch"}).json()
    e = client.post(
        f"/api/series/{s['id']}/editions", json={"year": 2099, "paper_date": "2098-12-01"}
    ).json()
    yield s, e
    detail = client.get(f"/api/series/{s['id']}")
    if detail.status_code == 200:
        for ed in detail.json()["editions"]:
            client.delete(f"/api/conferences/{ed['id']}")
        client.delete(f"/api/series/{s['id']}")
