"""Which build is running.

CI bakes APP_VERSION (the release, e.g. 0.4.1, or the branch name for builds of
main) and APP_COMMIT (the git SHA) into the image; a local run is "dev".
"""

from __future__ import annotations

import html
import os
import re

REPO = "https://github.com/MaastrichtU-IDS/conference-tracker"

VERSION = os.getenv("APP_VERSION", "").strip() or "dev"
COMMIT = os.getenv("APP_COMMIT", "").strip()
RELEASE = re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][\w.]+)?", VERSION) is not None


def label() -> str:
    """v0.4.1 for a release, main · 1a2b3c4 for a branch build, dev locally."""
    if RELEASE:
        return f"v{VERSION}"
    return f"{VERSION} · {COMMIT[:7]}" if COMMIT else VERSION


def link() -> str | None:
    if RELEASE:
        return f"{REPO}/tree/v{VERSION}"
    return f"{REPO}/commit/{COMMIT}" if COMMIT else None


def footer_html() -> str:
    text = html.escape(label())
    href = link()
    return f'<a href="{html.escape(href)}">{text}</a>' if href else text
