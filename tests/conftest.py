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
