"""Input and CLI secrecy, independent of PostgreSQL transaction tests."""

import secrets
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access import bootstrap as cli


def valid_data(**changes):
    return {
        "tenant_name": " Taller Carrillo ",
        "email": " ADMIN@EXAMPLE.COM ",
        "password": SecretStr(secrets.token_urlsafe(24)),
    } | changes


def test_input_normalizes_identity_but_preserves_password():
    password = SecretStr(" " + secrets.token_urlsafe(24) + " ")
    data = cli.BootstrapInput(**valid_data(password=password))
    assert data.tenant_name == "Taller Carrillo"
    assert data.email == "admin@example.com"
    assert data.password == password
    assert password.get_secret_value() not in repr(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_name": "   "},
        {"tenant_name": "a" * 201},
        {"email": "not-an-email"},
        {"email": "a\nb@example.com"},
        {"password": SecretStr("x" * 14)},
        {"password": SecretStr("x" * 129)},
        {"password": SecretStr("\ud800" * 15)},
        {"role": "admin"},
        {"tenant_id": "chosen-by-client"},
    ],
)
def test_invalid_bootstrap_input_is_rejected(changes):
    with pytest.raises(ValidationError):
        cli.BootstrapInput(**valid_data(**changes))


@pytest.mark.parametrize("length", [15, 128])
def test_password_length_boundaries_accept_unicode(length):
    assert cli.BootstrapInput(**valid_data(password=SecretStr("🔧" * length)))


def setup_terminal(monkeypatch, password=None, confirmation=None):
    password = password or secrets.token_urlsafe(24)
    confirmation = password if confirmation is None else confirmation
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    inputs = iter(["Taller Carrillo", "admin@example.com"])
    passwords = iter([password, confirmation])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: next(passwords))
    engine = MagicMock()
    monkeypatch.setattr(cli, "load_settings", lambda: object())
    monkeypatch.setattr(cli, "build_engine", lambda _: engine)
    return engine, password


def test_cli_success_disposes_engine_and_does_not_print_identity(monkeypatch, capsys):
    engine, password = setup_terminal(monkeypatch)
    operation = MagicMock()
    monkeypatch.setattr(cli, "bootstrap", operation)
    assert cli.main([]) == 0
    operation.assert_called_once()
    engine.dispose.assert_called_once()
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert "admin@example.com" not in output.out + output.err


def test_cli_rejects_piped_input_before_accessing_db(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    operation = MagicMock()
    monkeypatch.setattr(cli, "build_engine", operation)
    assert cli.main([]) == 2
    operation.assert_not_called()
    assert "interactive terminal" in capsys.readouterr().err


def test_cli_does_not_echo_unknown_arguments(capsys):
    secret = secrets.token_urlsafe(24)
    with pytest.raises(SystemExit) as caught:
        cli.main(["--password", secret])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert secret not in output.out + output.err


def test_mismatched_passwords_do_not_access_db(monkeypatch, capsys):
    engine, _ = setup_terminal(monkeypatch, confirmation=secrets.token_urlsafe(24))
    assert cli.main([]) == 2
    engine.dispose.assert_not_called()
    assert "no coinciden" in capsys.readouterr().err


@pytest.mark.parametrize("error_type", [SQLAlchemyError, RuntimeError])
def test_cli_sanitizes_failures_and_disposes_engine(monkeypatch, capsys, error_type):
    engine, password = setup_terminal(monkeypatch)
    operation = MagicMock(side_effect=error_type(password))
    monkeypatch.setattr(cli, "bootstrap", operation)
    assert cli.main([]) == 1
    engine.dispose.assert_called_once()
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert "no completado" in output.err


def test_cli_refuses_getpass_echo_fallback(monkeypatch, capsys):
    engine, _ = setup_terminal(monkeypatch)

    def unsafe_getpass(_):
        import warnings

        warnings.warn("cannot disable echo", cli.getpass.GetPassWarning, stacklevel=2)
        pytest.fail("A warning must abort before fallback input")

    monkeypatch.setattr(cli.getpass, "getpass", unsafe_getpass)
    assert cli.main([]) == 2
    engine.dispose.assert_not_called()
    assert "entrada oculta" in capsys.readouterr().err
