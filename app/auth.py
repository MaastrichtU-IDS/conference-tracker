"""OIDC sign-in, session handling and the current-user dependencies.

With OIDC configured, users sign in at /auth/login and a signed session cookie
carries their user id. Without it (local development, tests) the app runs in
single-user dev mode and every request is the same synthetic user.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_oauth = None


def oauth(settings: Settings):
    """Lazily build the Authlib client so the app imports without OIDC set."""
    global _oauth
    if _oauth is None and settings.oidc_configured:
        from authlib.integrations.starlette_client import OAuth

        _oauth = OAuth()
        _oauth.register(
            name="oidc",
            server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            client_kwargs={"scope": settings.oidc_scopes},
        )
    return _oauth


def _is_true(value) -> bool:
    # Booleans in an ID token are JSON booleans; some userinfo endpoints send strings.
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _groups(claims: dict) -> set[str]:
    raw = claims.get("groups")
    if not isinstance(raw, list):
        return set()
    # Keycloak sends "/name" when the mapper's "Full group path" is on.
    return {g.lstrip("/") for g in raw if isinstance(g, str)}


def _is_admin(settings: Settings, email: str, claims: dict, current: bool) -> bool:
    if settings.admin_groups:
        return bool(_groups(claims) & set(settings.admin_groups))
    if settings.admin_emails:
        return email.lower() in settings.admin_emails
    return current


def admit(db: Session, settings: Settings, claims: dict) -> User:
    """Decide from the provider's claims whether this person may sign in; return their user.

    Raises 403 for an unverified or out-of-domain email. Admin status is set
    from the current claims every time, so leaving the admin group takes effect
    at the next sign-in.
    """
    email = (claims.get("email") or "").strip()
    subject = claims.get("sub") or email
    if not subject:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Identity provider returned no subject")

    if settings.require_verified_email and not _is_true(claims.get("email_verified")):
        log.info("sign-in refused: email not verified (sub=%s)", subject)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your email address is not verified. Ask an administrator to verify it.",
        )

    domains = settings.allowed_email_domains
    if domains and not any(email.lower().endswith("@" + d) for d in domains):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not permitted to sign in")

    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    user = db.scalar(select(User).where(User.subject == subject))
    if user is None:
        user = User(subject=subject, email=email, name=name, is_admin=False)
        db.add(user)
    else:
        user.email = email or user.email
        user.name = name or user.name
    user.is_admin = _is_admin(settings, email, claims, bool(user.is_admin))
    db.commit()
    db.refresh(user)
    return user


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User | None:
    """The signed-in user, or the dev user when auth is disabled."""
    if not settings.auth_enabled:
        user = db.scalar(select(User).where(User.subject == "dev"))
        if user is None:
            user = User(
                subject="dev",
                email=settings.dev_user_email,
                name=settings.dev_user_name,
                is_admin=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    uid = request.session.get("uid")
    if not uid:
        return None
    return db.get(User, uid)


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


@router.get("/login")
async def login(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.auth_enabled:
        return RedirectResponse("/")
    client = oauth(settings).create_client("oidc")
    return await client.authorize_redirect(request, f"{settings.base_url}/auth/callback")


@router.get("/callback")
async def callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.auth_enabled:
        return RedirectResponse("/")
    client = oauth(settings).create_client("oidc")
    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001 - surface provider errors as 400
        log.warning("OIDC callback failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sign-in failed") from exc

    claims = token.get("userinfo") or {}
    if not claims:
        claims = await client.userinfo(token=token)

    user = admit(db, settings, claims)
    request.session["uid"] = user.id
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")
