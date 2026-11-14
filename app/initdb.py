"""Create the schema and load the seed dataset, then exit.

Run as the ArgoCD PreSync hook so one process does this before any app replica
starts. Idempotent: create_all skips existing tables and the seed loader is a
no-op once conferences exist.
"""

from __future__ import annotations

import logging
import sys

from .config import get_settings
from .db import SessionLocal, engine
from .models import Base
from .seed import seed_if_empty

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("initdb")


def main() -> int:
    settings = get_settings()
    log.info("creating schema if absent")
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        created = seed_if_empty(db, settings.seed_file)
    log.info("seeded %d conferences", created) if created else log.info("database already seeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
