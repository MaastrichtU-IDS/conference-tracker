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


def _upsert_user(db: Session, settings: Settings, subject: str, email: str, name: str) -> User:
    user = db.scalar(select(User).where(User.subject == subject))
    if user is None:
        user = User(subject=subject, email=email, name=name)
        db.add(user)
    else:
        user.email = email or user.email
        user.name = name or user.name
    user.is_admin = (
        email.lower() in settings.admin_emails if settings.admin_emails else user.is_admin
    )
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

    email = (claims.get("email") or "").strip()
    subject = claims.get("sub") or email
    if not subject:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Identity provider returned no subject")

    domains = settings.allowed_email_domains
    if domains and not any(email.lower().endswith("@" + d) for d in domains):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not permitted to sign in")

    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    user = _upsert_user(db, settings, subject, email, name)
    request.session["uid"] = user.id
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")
