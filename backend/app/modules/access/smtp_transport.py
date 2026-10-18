"""TLS-only SMTP in a killable child: total deadline covers DNS/TLS/auth/DATA.

Secrets only through stdin; no argv/env secrets or provider output logging.
A timeout or generic exception is UNKNOWN, never an assurance of non-delivery.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.modules.access.mail_delivery import TransportOutcome


class OutboundMessage(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)
    recipient: SecretStr = Field(exclude=True, repr=False)
    subject: str
    body: SecretStr = Field(exclude=True, repr=False)


class SmtpTransport:
    def __init__(self, config_file: Path):
        self._file = config_file

    def send_message(
        self, message: OutboundMessage, *, message_id: UUID, deadline: datetime
    ) -> TransportOutcome:
        remaining = min(20.0, (deadline - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            return TransportOutcome.NOT_SENT
        request = json.dumps(
            {
                "config_file": str(self._file),
                "recipient": message.recipient.get_secret_value(),
                "subject": message.subject,
                "body": message.body.get_secret_value(),
                "message_id": str(message_id),
                "timeout": remaining,
            }
        ).encode()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "app.modules.access.smtp_transport", "--child"],
                input=request,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=remaining,
                check=False,
            )
            if result.returncode != 0:
                return TransportOutcome.UNKNOWN
            return TransportOutcome(result.stdout.decode("ascii").strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return TransportOutcome.UNKNOWN


def _child():
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formatdate

    from pydantic import EmailStr, NameEmail, TypeAdapter

    accepted = False
    stage = "config"
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError
        data = json.loads(raw)
        config = json.loads(Path(data["config_file"]).read_text())
        if (
            set(config) != {"host", "port", "sender", "username", "password_file"}
            or config["port"] != 465
        ):
            raise ValueError
        host = config["host"]
        import re

        if not isinstance(host, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9.-]{1,252}", host
        ):
            raise ValueError
        sender = TypeAdapter(NameEmail).validate_python(config["sender"])
        recipient = str(TypeAdapter(EmailStr).validate_python(data["recipient"]))
        password = Path(config["password_file"]).read_text().strip()
        if not password or any(c in config["username"] for c in "\r\n"):
            raise ValueError
        message = EmailMessage()
        message["From"], message["To"] = str(sender), recipient
        message["Subject"] = data["subject"]
        message["Message-ID"] = (
            "<"
            + str(UUID(data["message_id"]))
            + "@"
            + sender.email.split("@", 1)[1]
            + ">"
        )
        message["Date"] = formatdate(localtime=False)
        message.set_content(data["body"])
        stage = "connect"
        with smtplib.SMTP_SSL(
            host,
            465,
            timeout=min(float(data["timeout"]), 10),
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(config["username"], password)
            stage = "send"
            refused = smtp.send_message(message)
            if refused:
                print(TransportOutcome.REJECTED.value)
                return
            accepted = True
        print(TransportOutcome.ACCEPTED.value)
    except (
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
        smtplib.SMTPDataError,
    ):
        print(TransportOutcome.REJECTED.value)
    except Exception:
        # Once DATA can have crossed the network, errors/QUIT failures are ambiguous.
        outcome = (
            TransportOutcome.ACCEPTED
            if accepted
            else TransportOutcome.NOT_SENT
            if stage in {"config", "connect"}
            else TransportOutcome.UNKNOWN
        )
        print(outcome.value)


if __name__ == "__main__":
    _child()
