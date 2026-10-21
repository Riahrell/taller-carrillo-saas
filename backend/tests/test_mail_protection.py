"""Synthetic mail encryption; never contacts a provider or stores actual keys."""

import secrets
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from uuid import uuid4

import pytest
from pydantic import SecretBytes, SecretStr

from app.modules.access.email_outbox import (
    MailProtection,
    MailProtectionError,
    MailPurpose,
)


@pytest.fixture
def crypto():
    keys = {name: SecretBytes(secrets.token_bytes(32)) for name in ("old", "new")}
    quota = SecretBytes(secrets.token_bytes(32))
    context = dict(
        user_id=uuid4(),
        challenge_id=uuid4(),
        expires_at=datetime(2026, 10, 17, tzinfo=UTC),
    )
    recipient = SecretStr("synthetic@example.invalid")
    token = SecretStr(secrets.token_urlsafe(32))
    old = MailProtection(keys, active_key_id="old", quota_key=quota)
    new = MailProtection(keys, active_key_id="new", quota_key=quota)
    envelope = old.encrypt(recipient=recipient, token=token, **context)
    return old, new, keys, quota, context, recipient, token, envelope


def test_mail_rotation_reads_old_writes_active_and_redacts_payload(crypto):
    old, new, _, _, ctx, recipient, token, envelope = crypto
    decoded = new.decrypt(envelope, **ctx)
    assert decoded.recipient == recipient and decoded.token == token
    assert decoded.model_dump() == {} and decoded.model_dump_json() == "{}"
    assert recipient.get_secret_value() not in repr(decoded)
    assert token.get_secret_value() not in repr(decoded)
    assert token.get_secret_value().encode() not in envelope.ciphertext
    assert recipient.get_secret_value().encode() not in envelope.ciphertext
    next_envelope = new.encrypt(recipient=recipient, token=token, **ctx)
    assert next_envelope.key_id == "new" and next_envelope.nonce != envelope.nonce
    assert old.decrypt(next_envelope, **ctx).token == token
    assert old.scopes(ctx["user_id"], ip_address("127.0.0.1")) == new.scopes(
        ctx["user_id"], ip_address("::ffff:127.0.0.1")
    )


@pytest.mark.parametrize(
    "part",
    ["user", "challenge", "expiry", "nonce", "ciphertext", "key", "unknown", "no_old"],
)
def test_mail_rejects_transplant_tamper_missing_key_and_changed_deadline(crypto, part):
    old, _, keys, quota, ctx, _, _, envelope = crypto
    if part == "user":
        ctx = ctx | {"user_id": uuid4()}
    elif part == "challenge":
        ctx = ctx | {"challenge_id": uuid4()}
    elif part == "expiry":
        ctx = ctx | {"expires_at": ctx["expires_at"] + timedelta(microseconds=1)}
    elif part in ("nonce", "ciphertext"):
        value = getattr(envelope, part)
        envelope = envelope.model_copy(update={part: bytes([value[0] ^ 1]) + value[1:]})
    elif part == "key":
        envelope = envelope.model_copy(update={"key_id": "new"})
    elif part == "unknown":
        envelope = envelope.model_copy(update={"key_id": "missing"})
    else:
        old = MailProtection({"new": keys["new"]}, active_key_id="new", quota_key=quota)
    with pytest.raises(MailProtectionError, match="^Mail protection unavailable$"):
        old.decrypt(envelope, **ctx)


@pytest.mark.parametrize("case", ["empty", "active", "length", "same_key"])
def test_mail_keys_have_no_defaults_and_distinct_quota_key(crypto, case):
    _, _, keys, quota, _, _, _, _ = crypto
    active = "old"
    if case == "empty":
        keys = {}
    elif case == "active":
        active = "absent"
    elif case == "length":
        quota = SecretBytes(b"x")
    else:
        quota = keys["old"]
    with pytest.raises(MailProtectionError):
        MailProtection(keys, active_key_id=active, quota_key=quota)


@pytest.mark.parametrize("purpose", list(MailPurpose))
def test_each_purpose_has_separate_authenticated_envelope(crypto, purpose):
    from app.modules.access.email_outbox import MailPurpose

    _, _, keys, quota, context, recipient, token, _ = crypto
    selected = MailProtection(
        keys, active_key_id="new", quota_key=quota, purpose=purpose
    )
    envelope = selected.encrypt(recipient=recipient, token=token, **context)
    assert selected.decrypt(envelope, **context).token == token
    other = MailProtection(
        keys,
        active_key_id="new",
        quota_key=quota,
        purpose=MailPurpose.PASSWORD_RESET
        if purpose != MailPurpose.PASSWORD_RESET
        else MailPurpose.EMAIL_LOGIN,
    )
    with pytest.raises(MailProtectionError):
        other.decrypt(envelope, **context)


def test_registration_code_hash_is_separate_from_login_and_has_shared_ip_quota(crypto):
    from app.modules.access.email_outbox import MailPurpose

    _, _, keys, quota, context, _, _, _ = crypto
    registration = MailProtection(
        keys,
        active_key_id="new",
        quota_key=quota,
        purpose=MailPurpose.EMPLOYEE_REGISTRATION,
    )
    login = MailProtection(
        keys, active_key_id="new", quota_key=quota, purpose=MailPurpose.EMAIL_LOGIN
    )
    assert registration.registration_code_hash(
        context["challenge_id"], "ABC234"
    ) != login.login_code_hash(context["challenge_id"], "ABC234")
    assert registration.scopes(
        context["user_id"], ip_address("192.0.2.50")
    ) == login.scopes(context["user_id"], ip_address("192.0.2.50"))
