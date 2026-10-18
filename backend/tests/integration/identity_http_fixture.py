"""Synthetic factor plus real HTTP TOTP; server clock advances between logins."""

from datetime import timedelta
from uuid import uuid4

import pyotp
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.access import (
    administration,
    authentication,
    identity_http,
    login_challenges,
    mfa_consumption,
    mfa_pending,
    mfa_sessions,
    mfa_verification,
    reauthentication,
    security_state,
    sessions,
)
from app.modules.access.mfa_crypto import generate_totp_seed
from app.modules.access.models import MfaCredential


def arrange_factor(state, app, monkeypatch):
    with Session(state["runtime"]) as session, session.begin():
        state["http_now"] = session.scalar(select(func.clock_timestamp()))
        seed = generate_totp_seed()
        identity = uuid4()
        envelope = app.state.identity.keyring.encrypt(
            seed, user_id=state["user"], credential_id=identity
        )
        session.add(
            MfaCredential(
                id=identity,
                user_id=state["user"],
                encryption_version=envelope.version,
                key_id=envelope.key_id,
                nonce=envelope.nonce,
                ciphertext=envelope.ciphertext,
                created_at=state["http_now"] - timedelta(minutes=1),
            )
        )
    state["http_seed"] = seed
    for module in (
        administration,
        reauthentication,
        authentication,
        identity_http,
        login_challenges,
        mfa_consumption,
        mfa_pending,
        mfa_sessions,
        mfa_verification,
        security_state,
        sessions,
    ):
        for name in ("_now", "_database_now"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda session: state["http_now"])


def next_factor(state):
    # Production still uses database time. Tests avoid sleeping thirty seconds
    # and must consume each code through the real verifier, never proof flags.
    state["http_now"] += timedelta(seconds=30)
    return pyotp.TOTP(state["http_seed"].base32().get_secret_value()).at(
        state["http_now"]
    )


def login_with_factor(client, state, policy):
    code = next_factor(state)
    headers = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
    }
    response = client.post(
        "/auth/login",
        json={
            "email": state["email"],
            "password": state["password"].get_secret_value(),
        },
        headers=headers,
    )
    assert response.status_code == 202 and response.json() == {"status": "mfa_totp"}
    return client.post("/auth/mfa/totp", json={"code": code}, headers=headers)
