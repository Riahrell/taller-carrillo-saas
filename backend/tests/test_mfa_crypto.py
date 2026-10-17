"""Synthetic/public fixtures only. No operational MFA credentials."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pyotp
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes, SecretStr, ValidationError

from app.modules.access import mfa_crypto as mfa

USER = UUID(int=1)
CREDENTIAL = UUID(int=2)
OTHER = UUID(int=3)
# Public RFC 6238 Appendix B SHA-1 test seed, never an application credential.
RFC_BYTES = b"12345678901234567890"
RFC_SEED = mfa.TotpSeed(value=SecretBytes(RFC_BYTES))
RFC_BASE32 = base64.b32encode(RFC_BYTES).decode("ascii")


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def match(code: str, seconds: float, **state) -> int | None:
    return mfa.match_totp(
        RFC_SEED,
        SecretStr(code),
        now=at(seconds),
        last_counter=state.get("last_counter"),
        last_verified_at=state.get("last_verified_at"),
    )


def ring(active="new", keys=None):
    # Deliberately synthetic constant keys, never loaded into application settings.
    if keys is None:
        keys = {"old": SecretBytes(bytes(range(32))), "new": SecretBytes(bytes(32))}
    return mfa.SeedKeyring(keys, active_key_id=active)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_vectors_and_six_digit_profile(seconds, expected):
    assert pyotp.TOTP(RFC_BASE32, digits=8).at(seconds) == expected
    assert match(expected[-6:], seconds) == seconds // 30


@pytest.mark.parametrize("seconds", [0, 29.999999, 30, 59.999999, 60, 90])
def test_step_boundaries_and_three_candidate_window(seconds):
    current = int(seconds) // 30
    otp = pyotp.TOTP(RFC_BASE32)
    for offset in [-2, -1, 0, 1, 2]:
        candidate = current + offset
        if candidate >= 0:
            actual = match(otp.generate_otp(candidate), seconds)
            assert actual == (candidate if abs(offset) <= 1 else None)


def test_leading_zero_preserved():
    # Counter 30 of the public SHA-1 test seed has a leading zero.
    code = pyotp.TOTP(RFC_BASE32).generate_otp(30)
    assert code.startswith("0")
    assert match(code, 900) == 30
    assert match(code.lstrip("0"), 900) is None


@pytest.mark.parametrize(
    "code",
    [
        "",
        "12345",
        "1234567",
        " 287082",
        "287082\n",
        "２８７０８２",
        "٢٨٧٠٨٢",
        "a" * 1000,
    ],
)
def test_invalid_totp_format(code):
    assert match(code, 59) is None


@pytest.mark.parametrize("last_counter", [1, 2, 3])
def test_consumed_or_older_counter_rejected(last_counter):
    assert (
        match("287082", 59, last_counter=last_counter, last_verified_at=at(58)) is None
    )


def test_next_counter_and_backward_clock():
    assert match("359152", 60, last_counter=1, last_verified_at=at(59)) == 2
    assert match("359152", 60, last_counter=1, last_verified_at=at(60.1)) is None
    assert match("359152", 60, last_counter=1, last_verified_at=at(61)) is None


def test_ambiguous_match_rejected_even_if_one_counter_consumed(monkeypatch):
    monkeypatch.setattr(pyotp.TOTP, "generate_otp", lambda self, counter: "123456")
    assert match("123456", 90) is None
    assert match("123456", 90, last_counter=3, last_verified_at=at(89)) is None


@pytest.mark.parametrize("now", [datetime(2026, 1, 1), at(-0.1), "2026-01-01"])
def test_invalid_server_time(now):
    with pytest.raises(ValueError):
        mfa.match_totp(
            RFC_SEED,
            SecretStr("123456"),
            now=now,
            last_counter=None,
            last_verified_at=None,
        )


@pytest.mark.parametrize(
    ("counter", "verified"),
    [(1, None), (None, at(59)), (True, at(59)), (-1, at(59)), (1.5, at(59))],
)
def test_invalid_persisted_state(counter, verified):
    with pytest.raises(ValueError):
        match("287082", 59, last_counter=counter, last_verified_at=verified)


def test_timezone_invariance():
    assert (
        mfa.match_totp(
            RFC_SEED,
            SecretStr("287082"),
            now=at(59).astimezone(timezone(timedelta(hours=-6))),
            last_counter=None,
            last_verified_at=None,
        )
        == 1
    )


def test_seed_generation_requests_160_random_bits(monkeypatch):
    lengths = []

    def random_bytes(length):
        lengths.append(length)
        return bytes(range(length))

    monkeypatch.setattr(mfa.secrets, "token_bytes", random_bytes)
    seed = mfa.generate_totp_seed()
    assert lengths == [20]
    assert base64.b32decode(seed.base32().get_secret_value()) == bytes(range(20))


@pytest.mark.parametrize("length", [0, 16, 19, 21, 32])
def test_seed_length_rejected(length):
    with pytest.raises(ValidationError):
        mfa.TotpSeed(value=SecretBytes(bytes(length)))


def test_aead_roundtrip_matches_independent_library_call():
    envelope = ring().encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    aad = b"carrillo:mfa:totp-seed:v1\x00" + USER.bytes + CREDENTIAL.bytes + b"new"
    assert (
        AESGCM(bytes(32)).decrypt(envelope.nonce, envelope.ciphertext, aad) == RFC_BYTES
    )
    assert ring().decrypt(envelope, user_id=USER, credential_id=CREDENTIAL) == RFC_SEED


def test_nonce_changes_and_randomness_size(monkeypatch):
    lengths = []

    def random_bytes(length):
        lengths.append(length)
        return bytes([len(lengths)]) * length

    monkeypatch.setattr(mfa.secrets, "token_bytes", random_bytes)
    first = ring().encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    second = ring().encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    assert lengths == [12, 12]
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


@pytest.mark.parametrize("field", ["nonce", "ciphertext", "version", "key_id"])
def test_tampered_envelope_fails_closed(field):
    envelope = ring().encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    values = {
        "nonce": bytes(12),
        "ciphertext": bytes(36),
        "version": 2,
        "key_id": "missing",
    }
    tampered = envelope.model_copy(update={field: values[field]})
    with pytest.raises(mfa.SeedProtectionError, match="^MFA seed protection failed$"):
        ring().decrypt(tampered, user_id=USER, credential_id=CREDENTIAL)


@pytest.mark.parametrize(("user", "credential"), [(OTHER, CREDENTIAL), (USER, OTHER)])
def test_aad_binds_user_and_credential(user, credential):
    envelope = ring().encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    with pytest.raises(mfa.SeedProtectionError):
        ring().decrypt(envelope, user_id=user, credential_id=credential)


def test_aad_binds_key_id_even_for_duplicate_key_material():
    keys = {"first": SecretBytes(bytes(32)), "second": SecretBytes(bytes(32))}
    keyring = ring("first", keys)
    envelope = keyring.encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    with pytest.raises(mfa.SeedProtectionError):
        keyring.decrypt(
            envelope.model_copy(update={"key_id": "second"}),
            user_id=USER,
            credential_id=CREDENTIAL,
        )


def test_ciphertext_from_other_purpose_rejected():
    nonce = bytes(12)
    ciphertext = AESGCM(bytes(32)).encrypt(
        nonce, RFC_BYTES, b"other-purpose\x00" + USER.bytes + CREDENTIAL.bytes + b"new"
    )
    envelope = mfa.EncryptedSeed(key_id="new", nonce=nonce, ciphertext=ciphertext)
    with pytest.raises(mfa.SeedProtectionError):
        ring().decrypt(envelope, user_id=USER, credential_id=CREDENTIAL)


def test_rotation_old_read_new_write_and_missing_or_wrong_keys():
    old = ring("old").encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    assert ring().decrypt(old, user_id=USER, credential_id=CREDENTIAL) == RFC_SEED
    assert (
        ring().encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL).key_id == "new"
    )
    for keys in [
        {"new": SecretBytes(bytes(32))},
        {"old": SecretBytes(bytes(32)), "new": SecretBytes(bytes(32))},
    ]:
        with pytest.raises(mfa.SeedProtectionError):
            ring(keys=keys).decrypt(old, user_id=USER, credential_id=CREDENTIAL)


@pytest.mark.parametrize(
    "keys",
    [
        {},
        {"new": SecretBytes(bytes(31))},
        {"new": SecretBytes(bytes(33))},
        {"new": SecretBytes(bytes(32)), "bad:id": SecretBytes(bytes(32))},
        {"bad:id": SecretBytes(bytes(32))},
        {"new": bytes(32)},
    ],
)
def test_invalid_keyring(keys):
    with pytest.raises(mfa.SeedProtectionError):
        ring(keys=keys)


def test_keyring_copies_external_mapping():
    keys = {"new": SecretBytes(bytes(32))}
    keyring = ring(keys=keys)
    keys.clear()
    envelope = keyring.encrypt(RFC_SEED, user_id=USER, credential_id=CREDENTIAL)
    assert keyring.decrypt(envelope, user_id=USER, credential_id=CREDENTIAL) == RFC_SEED


def test_recovery_generation_entropy_count_and_duplicate_retry(monkeypatch):
    values = iter([bytes(16), bytes(16), *(bytes([n]) * 16 for n in range(1, 10))])
    lengths = []

    def random_bytes(length):
        lengths.append(length)
        return next(values)

    monkeypatch.setattr(mfa.secrets, "token_bytes", random_bytes)
    codes = mfa.generate_recovery_codes()
    assert lengths == [16] * 11
    assert len(codes) == len({c.value.get_secret_value() for c in codes}) == 10
    assert all(len(c.value.get_secret_value()) == 26 for c in codes)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "A" * 25,
        "A" * 27,
        "a" * 26,
        "0" * 26,
        "Ａ" * 26,
        "A" * 25 + "B",
        " " + "A" * 26,
        "A" * 26 + "======",
    ],
)
def test_invalid_or_noncanonical_recovery_code(raw):
    with pytest.raises(ValidationError):
        mfa.RecoveryCode(value=SecretStr(raw))


def test_recovery_hash_domains_and_comparison():
    code = mfa.RecoveryCode(value=SecretStr("A" * 26))
    digest = mfa.recovery_digest(code, user_id=USER, set_id=CREDENTIAL)
    payload = (
        b"carrillo:mfa:offline-recovery:v1\x00"
        + USER.bytes
        + CREDENTIAL.bytes
        + b"A" * 26
    )
    assert digest == hashlib.sha256(payload).digest()
    assert digest != hashlib.sha256(b"A" * 26).digest()
    assert mfa.match_recovery_code(code, digest, user_id=USER, set_id=CREDENTIAL)
    assert not mfa.match_recovery_code(code, bytes(32), user_id=USER, set_id=CREDENTIAL)
    assert not mfa.match_recovery_code(code, digest, user_id=OTHER, set_id=CREDENTIAL)
    assert not mfa.match_recovery_code(code, digest, user_id=USER, set_id=OTHER)
    assert not mfa.match_recovery_code(code, digest, user_id=CREDENTIAL, set_id=USER)


@pytest.mark.parametrize("digest", [b"", bytes(31), bytes(33), "A" * 32])
def test_invalid_digest(digest):
    with pytest.raises(ValueError):
        mfa.match_recovery_code(
            mfa.RecoveryCode(value=SecretStr("A" * 26)),
            digest,
            user_id=USER,
            set_id=CREDENTIAL,
        )


@pytest.mark.parametrize(
    "model", [RFC_SEED, mfa.RecoveryCode(value=SecretStr("A" * 26))]
)
def test_no_secret_in_repr_or_serialization(model):
    raw = model.value.get_secret_value()
    text = raw.decode() if isinstance(raw, bytes) else raw
    assert text not in repr(model)
    assert text not in str(model)
    assert model.model_dump() == {}
    assert model.model_dump_json() == "{}"
    with pytest.raises(ValidationError):
        model.value = model.value


def test_validation_error_text_does_not_echo_secret():
    value = "synthetic-invalid-recovery-material"
    with pytest.raises(ValidationError) as caught:
        mfa.RecoveryCode(value=SecretStr(value))
    assert value not in str(caught.value)
    # Never log ValidationError.errors() with include_input=True (Pydantic default).
    assert value not in str(caught.value.errors(include_input=False))
