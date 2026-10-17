"""Explicit manual browser fixture; never collected or shipped in runtime image."""

import json
import os
import secrets
import time
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.access.models import Tenant, TenantMembership, User
from app.modules.access.passwords import hash_password

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_BROWSER_INTEGRATION") != "1",
        reason="Explicit isolated browser QA only",
    ),
]


def test_hold_browser_identity(http_identity):
    state = http_identity
    with Session(state["runtime"]) as session, session.begin():
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
            {"email": state["email"], "password": state["password"].get_secret_value()},
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
