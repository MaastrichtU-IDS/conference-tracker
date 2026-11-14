"""Application settings, read from the environment."""

from __future__ import annotations

import os
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # --- core ---
    app_name: str = os.getenv("APP_NAME", "2027 Conference Tracker")
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    secret_key: str = os.getenv("SECRET_KEY", "dev-insecure-change-me")
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://tracker:tracker@localhost:5432/tracker"
    )

    # --- OIDC ---
    oidc_issuer: str | None = os.getenv("OIDC_ISSUER") or None
    oidc_client_id: str | None = os.getenv("OIDC_CLIENT_ID") or None
    oidc_client_secret: str | None = os.getenv("OIDC_CLIENT_SECRET") or None
    oidc_scopes: str = os.getenv("OIDC_SCOPES", "openid email profile")
    # Comma-separated email domains allowed to sign in. Empty = any.
    allowed_email_domains: tuple[str, ...] = tuple(
        x.strip().lower() for x in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",") if x.strip()
    )
    # Comma-separated emails granted admin (conference/milestone editing).
    admin_emails: tuple[str, ...] = tuple(
        x.strip().lower() for x in os.getenv("ADMIN_EMAILS", "").split(",") if x.strip()
    )

    # When OIDC is not configured the app runs in single-user dev mode.
    dev_auth: bool = _bool("DEV_AUTH", False)
    dev_user_email: str = os.getenv("DEV_USER_EMAIL", "dev@localhost")
    dev_user_name: str = os.getenv("DEV_USER_NAME", "Dev User")

    seed_on_start: bool = _bool("SEED_ON_START", True)
    seed_file: str = os.getenv("SEED_FILE", "seed/conferences.json")

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def auth_enabled(self) -> bool:
        """True when real sign-in is required; False in dev mode."""
        return self.oidc_configured and not self.dev_auth


@lru_cache
def get_settings() -> Settings:
    return Settings()
