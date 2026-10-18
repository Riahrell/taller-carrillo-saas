"""Explicit manual browser fixture; never collected or shipped in runtime image."""

import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.modules.access.identity_http import IdentityAccess
from app.modules.access.mfa_crypto import generate_totp_seed
from app.modules.access.models import (
    AuditEvent,
    MfaCredential,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password
from app.modules.catalog.models import Category, Product

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_BROWSER_INTEGRATION") != "1",
        reason="Explicit isolated browser QA only",
    ),
]


def test_hold_browser_identity(new_identity_scope):
    state = new_identity_scope
    seed = generate_totp_seed()
    credential_id = uuid4()
    keyring = (
        IdentityAccess(state["runtime"], state["settings"].identity_keys_file)
        .require()
        .keyring
    )
    envelope = keyring.encrypt(seed, user_id=state["user"], credential_id=credential_id)
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            MfaCredential(
                id=credential_id,
                user_id=state["user"],
                encryption_version=envelope.version,
                key_id=envelope.key_id,
                nonce=envelope.nonce,
                ciphertext=envelope.ciphertext,
                created_at=session.scalar(select(func.clock_timestamp())),
            )
        )
        session.execute(
            update(Tenant)
            .where(Tenant.id == state["tenant"])
            .values(name="Carrillo · Pruebas A")
        )
        session.execute(
            update(Tenant)
            .where(Tenant.id == state["other"])
            .values(name="Carrillo · Pruebas B")
        )
        session.add(
            TenantMembership(
                tenant_id=state["other"], user_id=state["user"], role="mechanic"
            )
        )
        session.add(
            TenantMembership(
                tenant_id=state["tenant"], user_id=state["foreign"], role="cashier"
            )
        )
        session.execute(
            update(User)
            .where(User.id == state["foreign"])
            .values(password_hash=hash_password(SecretStr(secrets.token_urlsafe(32))))
        )
    target = Path("/tmp/carrillo-browser-fixture.json")
    # Synthetic, random test credentials stay in a private file, never stdout/logs.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(
            {
                "email": state["email"],
                "password": state["password"].get_secret_value(),
                "totp_seed": seed.base32().get_secret_value(),
                "new_email": state["prefix"] + "staff@example.com",
                "new_password": secrets.token_urlsafe(32),
            },
            stream,
        )
    print(
        "Isolated browser fixture ready; credentials in private container tmpfs.",
        flush=True,
    )
    try:
        deadline = time.monotonic() + 1800
        while not Path("/tmp/carrillo-browser-finished").exists():
            if time.monotonic() >= deadline:
                pytest.fail("Browser QA window expired")
            time.sleep(0.25)
    finally:
        target.unlink(missing_ok=True)
        with state["owner"].begin() as connection:
            for model in (AuditEvent, Product, Category):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )
