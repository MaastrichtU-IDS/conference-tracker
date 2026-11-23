"""Who may sign in, and who becomes an admin, from the identity provider's claims.

Exercises `admit()`, the decision the OIDC callback makes once the provider has
answered, with hand-written claims so no provider is needed.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.auth import admit
from app.config import Settings
from app.db import SessionLocal

DOMAIN = "maastrichtuniversity.nl"


def _settings(**overrides) -> Settings:
    s = Settings()
    s.allowed_email_domains = (DOMAIN,)
    s.admin_emails = ()
    s.admin_groups = ("conference-tracker-admins",)
    s.require_verified_email = True
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _claims(**overrides) -> dict:
    c = {
        "sub": f"kc-{uuid.uuid4()}",
        "email": f"member.{uuid.uuid4().hex[:6]}@{DOMAIN}",
        "email_verified": True,
        "name": "A Member",
        "groups": [],
    }
    c.update(overrides)
    return c


@pytest.fixture
def db(client):  # `client` creates the schema
    with SessionLocal() as s:
        yield s


def _refused(db, settings, claims) -> HTTPException:
    with pytest.raises(HTTPException) as e:
        admit(db, settings, claims)
    return e.value


# --- verified email -----------------------------------------------------------


def test_verified_member_signs_in_without_admin(db):
    user = admit(db, _settings(), _claims())
    assert user.id and not user.is_admin


@pytest.mark.parametrize("verified", [False, None, "false", "", 0])
def test_unverified_email_is_refused(db, verified):
    claims = _claims(email_verified=verified)
    if verified is None:
        del claims["email_verified"]
    assert _refused(db, _settings(), claims).status_code == 403


def test_string_true_counts_as_verified(db):
    # Some providers serialise booleans in userinfo as strings.
    assert admit(db, _settings(), _claims(email_verified="true")).id


def test_verification_check_can_be_switched_off(db):
    user = admit(db, _settings(require_verified_email=False), _claims(email_verified=False))
    assert user.id


def test_wrong_domain_is_refused_even_when_verified(db):
    claims = _claims(email="someone@gmail.com")
    assert _refused(db, _settings(), claims).status_code == 403


# --- admin by group -------------------------------------------------------------


def test_group_member_is_admin(db):
    user = admit(db, _settings(), _claims(groups=["conference-tracker-admins", "other"]))
    assert user.is_admin


def test_full_group_path_also_matches(db):
    # Keycloak's "Full group path" mapper setting sends "/conference-tracker-admins".
    assert admit(db, _settings(), _claims(groups=["/conference-tracker-admins"])).is_admin


def test_missing_groups_claim_is_not_admin(db):
    claims = _claims()
    del claims["groups"]
    assert not admit(db, _settings(), claims).is_admin


def test_group_name_must_match_exactly(db):
    for groups in (["conference-tracker-admins-old"], ["Conference-Tracker-Admins"], "x"):
        assert not admit(db, _settings(), _claims(groups=groups)).is_admin


def test_admin_email_is_ignored_when_admin_groups_are_set(db):
    claims = _claims()
    settings = _settings(admin_emails=(claims["email"],))
    assert not admit(db, settings, claims).is_admin


def test_leaving_the_group_revokes_admin_at_next_sign_in(db):
    claims = _claims(groups=["conference-tracker-admins"])
    assert admit(db, _settings(), claims).is_admin
    claims["groups"] = []
    assert not admit(db, _settings(), claims).is_admin


def test_admin_emails_still_work_without_admin_groups(db):
    claims = _claims()
    settings = _settings(admin_groups=(), admin_emails=(claims["email"],))
    assert admit(db, settings, claims).is_admin


# --- identity -------------------------------------------------------------------


def test_same_subject_is_the_same_user(db):
    claims = _claims()
    first = admit(db, _settings(), claims)
    claims["name"] = "Renamed Member"
    second = admit(db, _settings(), claims)
    assert first.id == second.id and second.name == "Renamed Member"


def test_no_subject_and_no_email_is_refused(db):
    claims = _claims(email="", sub="")
    assert _refused(db, _settings(require_verified_email=False), claims).status_code in (400, 403)
