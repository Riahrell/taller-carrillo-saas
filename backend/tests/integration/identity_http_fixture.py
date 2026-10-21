"""Synthetic factor plus real HTTP TOTP; server clock advances between logins."""

from datetime import timedelta
from uuid import uuid4

import pyotp
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.access import (
    administration,
    authentication,
    email_login,
    email_outbox,
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
        email_login,
        email_outbox,
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


def begin_legacy_login(client, state, policy):
    # Explicit legacy adapter setup: authenticate the synthetic password through
    # the real internal TOTP producer. The production /auth/login now uses mail;
    # it is exercised separately by email-login HTTP tests.
    from ipaddress import ip_address

    from starlette.datastructures import Headers

    from app.modules.access.authentication import Credentials, PendingMfaLogin
    from app.modules.access.identity_http import browser_binding

    binding = browser_binding(
        policy,
        Headers(
            {
                "cookie": "; ".join(
                    f"{key}={value}" for key, value in client.cookies.items()
                )
            }
        ),
    )
    result = client.app.state.access.authenticator.begin_mfa_login(
        Credentials(email=state["email"], password=state["password"]),
        client_ip=ip_address("127.0.0.1"),
        browser_binding=binding,
    )
    if isinstance(result, PendingMfaLogin):
        client.cookies.pop(policy.challenge_cookie, None)
        client.cookies.set(
            policy.challenge_cookie,
            result.token.get_secret_value(),
            domain=next(
                c.domain for c in client.cookies.jar if c.name == policy.csrf_cookie
            ),
            path="/",
        )
    return result


def login_with_legacy_factor(client, state, policy):
    code = next_factor(state)
    begin_legacy_login(client, state, policy)
    return client.post(
        "/auth/mfa/totp",
        json={"code": code},
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        },
    )


def complete_email_login(client, state, policy, response):
    """Real public mail flow with capture transport, synthetic test recipients."""
    if response.status_code != 202 or response.json().get("status") != "email_code":
        return response
    code = request_email_code(client, state, policy)
    return client.post(
        "/auth/email-login/confirm",
        json={"code": code},
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies[policy.csrf_cookie],
            "Sec-Fetch-Site": "same-origin",
        },
    )


def request_email_code(client, state, policy):
    """Capture a synthetic code after the real request and worker paths."""
    import re

    from app.modules.access.mail_delivery import TransportOutcome

    headers = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "Sec-Fetch-Site": "same-origin",
    }
    issued = client.post("/auth/email-login/request", json={}, headers=headers)
    assert issued.status_code == 204
    captured = []

    class Capture:
        def send_message(self, message, **_):
            assert message.recipient.get_secret_value() == state["email"]
            captured.append(message.body.get_secret_value())
            return TransportOutcome.ACCEPTED

    client.app.state.identity.email_login.dispatch_one(Capture())
    assert len(captured) == 1
    match = re.search(r"es: ([A-Z0-9]{6})\n", captured[0])
    assert match
    return match[1]
