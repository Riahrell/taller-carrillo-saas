"""Composition of the completed identity kernels; no secret default or fallback."""

import base64
import hashlib
import json
from pathlib import Path

from pydantic import SecretBytes, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access import mfa_pending
from app.modules.access.authentication import AuthenticationUnavailable
from app.modules.access.email_outbox import MailProtection, MailPurpose
from app.modules.access.enrollment_email import EnrollmentEmailService
from app.modules.access.mailbox_service import MailboxService
from app.modules.access.mfa_crypto import SeedKeyring
from app.modules.access.mfa_enrollment import MfaEnrollmentService
from app.modules.access.mfa_recovery import MfaRecoveryService
from app.modules.access.mfa_regeneration import MfaCodeRegenerator
from app.modules.access.mfa_replacement import MfaReplacementService
from app.modules.access.mfa_sessions import MfaSessionIssuer
from app.modules.access.mfa_verification import MfaVerifier, _timeouts
from app.modules.access.models import MfaPending
from app.modules.access.password_reset import PasswordResetConsumer, PasswordResetIssuer
from app.modules.access.sessions import _lock_user


def browser_binding(policy, headers) -> SecretStr:
    original = policy.preauth_binding(headers).get_secret_value().encode("ascii")
    return SecretStr(
        base64.urlsafe_b64encode(
            hashlib.sha256(b"carrillo:identity-browser:v1\x00" + original).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )


class IdentityAccess:
    def __init__(self, engine, key_file: Path | None):
        self._factory = create_session_factory(engine)
        self.sessions = MfaSessionIssuer(engine)
        self.recovery = MfaRecoveryService(engine)
        self.regenerate = MfaCodeRegenerator(engine)
        self.reset_consumer = PasswordResetConsumer(engine)
        self.configured = key_file is not None
        if not self.configured:
            return
        try:
            if key_file.stat().st_size > 16384:
                raise ValueError
            data = json.loads(key_file.read_text())
            if set(data) != {
                "seed_keys",
                "active_seed_key",
                "mail_keys",
                "active_mail_key",
                "quota_key",
            }:
                raise ValueError
            self.keyring = SeedKeyring(
                {
                    key: SecretBytes(bytes.fromhex(value))
                    for key, value in data["seed_keys"].items()
                },
                active_key_id=data["active_seed_key"],
            )
            self.mail = {}
            for purpose in MailPurpose:
                self.mail[purpose] = MailProtection(
                    {
                        key: SecretBytes(bytes.fromhex(value))
                        for key, value in data["mail_keys"].items()
                    },
                    active_key_id=data["active_mail_key"],
                    quota_key=SecretBytes(bytes.fromhex(data["quota_key"])),
                    purpose=purpose,
                )
            from app.modules.access.email_login import EmailLogin

            self.email_login = EmailLogin(engine, self.mail[MailPurpose.EMAIL_LOGIN])
            self.verifier = MfaVerifier(engine, keyring=self.keyring)
            self.enrollment_mail = EnrollmentEmailService(
                engine, protection=self.mail[MailPurpose.ENROLLMENT]
            )
            self.enrollment = MfaEnrollmentService(engine, keyring=self.keyring)
            self.replacement = MfaReplacementService(engine, keyring=self.keyring)
            from app.modules.access.mfa_rotation import MfaRotationService

            self.rotation = MfaRotationService(engine, keyring=self.keyring)
            self.reset_issuer = PasswordResetIssuer(
                engine, protection=self.mail[MailPurpose.PASSWORD_RESET]
            )
            from app.modules.access.reset_inbox import ResetInbox

            self.reset_inbox = ResetInbox(
                engine,
                protection=self.mail[MailPurpose.RESET_REQUEST],
                issuer=self.reset_issuer,
            )
            self.mailbox = MailboxService(
                engine, protection=self.mail[MailPurpose.MAILBOX_VERIFICATION]
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise AuthenticationUnavailable(
                "Identity configuration unavailable"
            ) from None

    def require(self):
        if not self.configured:
            raise AuthenticationUnavailable("Identity configuration unavailable")
        return self

    def cancel(self, binding: SecretStr):
        digest = mfa_pending._hash(binding, "mfa-pending-browser-v1:")
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                users = session.scalars(
                    select(MfaPending.user_id)
                    .where(MfaPending.binding_hash == digest)
                    .distinct()
                ).all()
                for user_id in sorted(users):
                    _lock_user(session, user_id)
                    mfa_pending._close_browser_pendings(
                        session, user_id=user_id, binding=binding
                    )
        except SQLAlchemyError:
            raise AuthenticationUnavailable("Authentication unavailable") from None
