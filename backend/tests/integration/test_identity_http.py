"""Real MFA HTTP entry never grants a session from password or client context."""

import time
from ipaddress import ip_address
from uuid import uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access.mfa_crypto import generate_totp_seed
from app.modules.access.models import MfaCredential, User

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def identity_web(http_identity, monkeypatch):
    state = http_identity
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    app = create_app(state["settings"])
    with TestClient(
        app,
        base_url=state["settings"].public_origin,
        client=("127.0.0.2", 12345),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as client:
        yield client, state, app


def post(client, app, path, body, *, expected=None):
    policy = app.state.http_security
    return client.post(
        path,
        json=body,
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies.get(policy.csrf_cookie, ""),
            "Sec-Fetch-Site": "same-origin",
            **({"X-Expected-Membership": str(expected)} if expected else {}),
        },
    )


def login(client, state, app):
    assert client.get("/auth/csrf", headers=READ).status_code == 204
    return post(
        client,
        app,
        "/auth/login",
        dict(email=state["email"], password=state["password"].get_secret_value()),
    )


def factor(state, app):
    seed = generate_totp_seed()
    identity = uuid4()
    envelope = app.state.identity.keyring.encrypt(
        seed, user_id=state["user"], credential_id=identity
    )
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, state["user"])
        session.add(
            MfaCredential(
                id=identity,
                user_id=user.id,
                encryption_version=envelope.version,
                key_id=envelope.key_id,
                nonce=envelope.nonce,
                ciphertext=envelope.ciphertext,
                created_at=session.scalar(select(func.clock_timestamp())),
            )
        )
    return seed


def test_admin_without_factor_only_receives_restricted_enrollment(identity_web):
    client, state, app = identity_web
    response = login(client, state, app)
    assert response.status_code == 202
    assert response.json() == {"status": "mfa_enrollment"}
    assert app.state.http_security.session_cookie not in client.cookies
    assert client.get("/auth/session", headers=READ).status_code == 401
    assert "HTTP company" not in response.text


def test_totp_creates_session_but_reused_code_cannot_login_again(identity_web):
    client, state, app = identity_web
    seed = factor(state, app)
    assert login(client, state, app).json() == {"status": "mfa_totp"}
    code = pyotp.TOTP(seed.base32().get_secret_value()).now()
    response = post(client, app, "/auth/mfa/totp", {"code": code})
    assert response.status_code == 200
    assert response.json() == {"status": "authenticated"}
    assert client.get("/auth/session", headers=READ).status_code == 200
    token = client.cookies[app.state.http_security.session_cookie]
    sessions = client.get("/auth/sessions", headers=READ)
    assert sessions.status_code == 200 and token not in sessions.text
    client.cookies.clear()  # Independent browser context, no authenticated cookie.
    assert login(client, state, app).json() == {"status": "mfa_totp"}
    assert post(client, app, "/auth/mfa/totp", {"code": code}).status_code == 401


def test_mfa_mutation_requires_csrf_and_rejects_client_authority(identity_web):
    client, state, app = identity_web
    factor(state, app)
    assert login(client, state, app).status_code == 202
    assert client.post("/auth/mfa/totp", json={"code": "123456"}).status_code == 403
    assert (
        post(
            client,
            app,
            "/auth/mfa/totp",
            {"code": "123456", "tenant_id": str(state["tenant"]), "role": "admin"},
        ).status_code
        == 422
    )
    assert app.state.http_security.session_cookie not in client.cookies


def test_rotation_preserves_old_factor_until_atomic_activation(identity_web):
    client, state, app = identity_web
    original = factor(state, app)
    assert login(client, state, app).json() == {"status": "mfa_totp"}
    assert (
        post(
            client,
            app,
            "/auth/mfa/totp",
            {"code": pyotp.TOTP(original.base32().get_secret_value()).now()},
        ).status_code
        == 200
    )
    assert post(client, app, "/auth/mfa/rotation/begin", {}).status_code == 428
    assert (
        post(
            client,
            app,
            "/auth/mfa/rotation/begin",
            {},
            expected=state["foreign_membership"],
        ).status_code
        != 200
    )
    setup = post(
        client, app, "/auth/mfa/rotation/begin", {}, expected=state["membership"]
    )
    assert setup.status_code == 200
    assert setup.headers["cache-control"] == "no-store"
    payload = setup.json()
    assert payload["qr"].startswith("data:image/svg+xml;base64,")
    otp = pyotp.TOTP(payload["seed"])
    counter = int(time.time()) // 30
    valid = {otp.generate_otp(counter + offset) for offset in (-1, 0, 1)}
    wrong = next(str(i).zfill(6) for i in range(10) if str(i).zfill(6) not in valid)
    assert (
        post(
            client,
            app,
            "/auth/mfa/rotation/activate",
            {"code": wrong},
            expected=state["membership"],
        ).status_code
        == 401
    )
    with Session(state["runtime"]) as session:
        assert (
            len(
                session.scalars(
                    select(MfaCredential).where(
                        MfaCredential.user_id == state["user"],
                        MfaCredential.revoked_at.is_(None),
                    )
                ).all()
            )
            == 1
        )
    activated = post(
        client,
        app,
        "/auth/mfa/rotation/activate",
        {"code": otp.now()},
        expected=state["membership"],
    )
    assert activated.status_code == 200
    assert len(activated.json()["codes"]) == 10
    assert client.get("/auth/session", headers=READ).status_code == 401
    assert app.state.http_security.session_cookie not in client.cookies
    with Session(state["runtime"]) as session:
        factors = session.scalars(
            select(MfaCredential).where(MfaCredential.user_id == state["user"])
        ).all()
        assert len(factors) == 2
        assert sum(item.revoked_at is None for item in factors) == 1


def test_reauthentication_rotates_cookie_without_extending_absolute_lifetime(
    identity_web,
):
    from datetime import UTC, datetime

    from app.modules.access.models import SessionRecord

    client, state, app = identity_web
    seed = factor(state, app)
    assert login(client, state, app).json() == {"status": "mfa_totp"}
    otp = pyotp.TOTP(seed.base32().get_secret_value())
    assert post(client, app, "/auth/mfa/totp", {"code": otp.now()}).status_code == 200
    cookie = client.cookies[app.state.http_security.session_cookie]
    with Session(state["runtime"]) as session:
        before = session.scalar(
            select(SessionRecord).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
        expiry = before.expires_at
    data = dict(password=state["password"].get_secret_value(), code=otp.now())
    assert (
        post(
            client,
            app,
            "/auth/mfa/reauthenticate",
            data,
            expected=state["foreign_membership"],
        ).status_code
        != 204
    )
    # A previously consumed factor code cannot confirm identity a second time.
    assert (
        post(
            client, app, "/auth/mfa/reauthenticate", data, expected=state["membership"]
        ).status_code
        == 401
    )
    data["code"] = otp.at(datetime.now(UTC), counter_offset=1)
    response = post(
        client, app, "/auth/mfa/reauthenticate", data, expected=state["membership"]
    )
    assert response.status_code == 204
    assert client.cookies[app.state.http_security.session_cookie] != cookie
    with Session(state["runtime"]) as session:
        after = session.scalar(
            select(SessionRecord).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
        assert after.expires_at == expiry
        assert after.mfa_reauthenticated_at is not None


def test_general_mailbox_confirmation_is_bound_to_requesting_session(identity_web):
    from app.modules.access.email_outbox import MailEnvelope, MailPurpose
    from app.modules.access.models import (
        MailboxChallenge,
        TenantMembership,
        VerifiedMailbox,
    )

    client, state, app = identity_web
    with Session(state["runtime"]) as session, session.begin():
        session.get(TenantMembership, state["membership"]).role = "read_only"
    assert login(client, state, app).status_code == 200
    assert (
        post(
            client, app, "/auth/mailbox/request", {}, expected=state["membership"]
        ).status_code
        == 204
    )
    with Session(state["runtime"]) as session:
        row = session.scalar(
            select(MailboxChallenge).where(MailboxChallenge.user_id == state["user"])
        )
        payload = app.state.identity.mail[MailPurpose.MAILBOX_VERIFICATION].decrypt(
            MailEnvelope(key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext),
            user_id=row.user_id,
            challenge_id=row.id,
            expires_at=row.expires_at,
        )
    body = {"token": payload.token.get_secret_value()}
    with TestClient(
        create_app(state["settings"]),
        base_url=state["settings"].public_origin,
        client=("127.0.0.2", 12346),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as other:
        assert login(other, state, app).status_code == 200
        assert (
            post(
                other, app, "/auth/mailbox/confirm", body, expected=state["membership"]
            ).status_code
            == 401
        )
    assert (
        post(
            client, app, "/auth/mailbox/confirm", body, expected=state["membership"]
        ).status_code
        == 204
    )
    assert (
        post(
            client, app, "/auth/mailbox/confirm", body, expected=state["membership"]
        ).status_code
        == 401
    )
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(VerifiedMailbox).where(
                    VerifiedMailbox.user_id == state["user"],
                    VerifiedMailbox.invalidated_at.is_(None),
                )
            )
            is not None
        )
