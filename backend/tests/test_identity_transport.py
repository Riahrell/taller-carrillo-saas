"""Delivery ambiguity must never turn into an automatic duplicate send."""

import io
import json
import smtplib
import ssl
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.modules.access import smtp_transport as transport
from app.modules.access.mail_delivery import TransportOutcome


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("connect", TransportOutcome.NOT_SENT),
        ("auth", TransportOutcome.NOT_SENT),
        ("data", TransportOutcome.UNKNOWN),
        ("refused", TransportOutcome.REJECTED),
        ("quit", TransportOutcome.ACCEPTED),
        (None, TransportOutcome.ACCEPTED),
    ],
)
def test_smtp_tls_and_delivery_boundary(
    tmp_path, monkeypatch, capsys, failure, expected
):
    password = tmp_path / "password"
    password.write_text("synthetic-only")
    config = tmp_path / "smtp.json"
    config.write_text(
        json.dumps(
            dict(
                host="smtp.example.com",
                port=465,
                sender="sender@example.com",
                username="synthetic",
                password_file=str(password),
            )
        )
    )
    request = dict(
        config_file=str(config),
        recipient="recipient@example.com",
        subject="Test",
        body="synthetic body",
        message_id=str(uuid4()),
        timeout=2,
    )
    monkeypatch.setattr(
        transport.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())),
    )

    class Server:
        def __init__(self, host, port, *, timeout, context):
            assert host == "smtp.example.com" and port == 465
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            if failure == "connect":
                raise ssl.SSLCertVerificationError("synthetic")

        def __enter__(self):
            return self

        def login(self, username, password):
            if failure == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"synthetic")

        def send_message(self, message):
            if failure == "data":
                raise OSError("synthetic ambiguity")
            if failure == "refused":
                raise smtplib.SMTPRecipientsRefused(
                    {"recipient@example.com": (550, b"no")}
                )
            return {}

        def __exit__(self, *args):
            if failure == "quit":
                raise OSError("synthetic QUIT failure")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Server)
    transport._child()
    assert capsys.readouterr().out.strip() == expected.value


def test_supervisor_deadline_is_ambiguous_and_secrets_are_stdin(tmp_path, monkeypatch):
    def run(args, **kwargs):
        assert args[-1] == "--child"
        assert "private-recipient" not in " ".join(args)
        assert "private-body" not in " ".join(args)
        assert b"private-recipient" in kwargs["input"]
        assert kwargs["timeout"] <= 20
        assert kwargs["stderr"] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    message = transport.OutboundMessage(
        recipient=SecretStr("private-recipient@example.com"),
        subject="Test",
        body=SecretStr("private-body"),
    )
    assert "private-body" not in repr(message)
    assert (
        transport.SmtpTransport(tmp_path / "config").send_message(
            message,
            message_id=uuid4(),
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        )
        == TransportOutcome.UNKNOWN
    )
