"""One backend-image worker; SMTP configuration must be provisioned externally."""

import argparse
import os
import signal
import time
from pathlib import Path

from pydantic import SecretStr

from app.core.config import load_settings
from app.core.database import build_engine
from app.modules.access.email_outbox import MailPurpose
from app.modules.access.identity_delivery import IdentityMailDispatcher
from app.modules.access.identity_http import IdentityAccess
from app.modules.access.mail_delivery import MailDispatcher
from app.modules.access.smtp_transport import OutboundMessage, SmtpTransport


class EnrollmentTransport:
    def __init__(self, transport, origin):
        self.transport, self.origin = transport, origin

    def send(self, payload, *, message_id, expires_at, deadline):
        secret = payload.token.get_secret_value()
        body = (
            f"Verifica tu buzón en {self.origin}/verificar#enrollment={secret}\n\n"
            f"Código: {secret}\n\n"
            "Confírmalo en el navegador que solicitó el mensaje. "
            "Este enlace no inicia sesión ni activa MFA."
        )
        return self.transport.send_message(
            OutboundMessage(
                recipient=payload.recipient,
                subject="Incorpora tu autenticador",
                body=SecretStr(body),
            ),
            message_id=message_id,
            deadline=deadline,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = load_settings()
    engine = build_engine(config)
    try:
        identity = IdentityAccess(engine, config.identity_keys_file).require()
        transport = SmtpTransport(Path(os.environ["SMTP_CONFIG_FILE"]))
        dispatcher = IdentityMailDispatcher(
            engine,
            identity=identity,
            transport=transport,
            origin=config.public_origin,
            environment=config.app_env,
        )
        enrollment = MailDispatcher(
            engine,
            protection=identity.mail[MailPurpose.ENROLLMENT],
            transport=EnrollmentTransport(transport, config.public_origin),
        )
        stopping = False

        def stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        last_maintenance = 0.0
        while not stopping:
            try:
                identity.email_login.dispatch_one(transport)
                identity.employee_registration.dispatch_one(transport)
                identity.reset_inbox.process_one()
                enrollment.run_one()
                dispatcher.dispatch_one()
                if time.monotonic() - last_maintenance >= 60:
                    from app.modules.access.maintenance import maintain

                    maintain(engine)
                    last_maintenance = time.monotonic()
            except Exception:
                print("Identity delivery unavailable; no secret details.", flush=True)
            if args.once:
                break
            for _ in range(10):
                if stopping:
                    break
                time.sleep(0.1)
    except Exception:
        parser.exit(1, "Identity worker configuration unavailable.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
