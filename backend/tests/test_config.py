from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import ConfigurationError, Settings, load_settings
from app.main import create_app


def test_environment_is_required() -> None:
    with pytest.raises(ConfigurationError, match="app_env"):
        create_app()


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_valid_environment_is_loaded(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    settings = load_settings()
    assert settings.app_env == environment
    assert settings.docs_enabled is False


def test_unknown_environment_fails_without_disclosing_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_value = "invalid-environment-sensitive-value"
    monkeypatch.setenv("APP_ENV", invalid_value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert invalid_value not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_invalid_boolean_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DOCS_ENABLED", "not-a-boolean")
    with pytest.raises(ConfigurationError, match="docs_enabled"):
        load_settings()


def test_production_rejects_interactive_documentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DOCS_ENABLED", "true")
    with pytest.raises(ConfigurationError):
        create_app()


def test_dotenv_is_loaded_and_environment_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Path(".env").write_text("APP_ENV=development\nDOCS_ENABLED=false\n")
    assert load_settings().app_env == "development"
    monkeypatch.setenv("APP_ENV", "test")
    assert load_settings().app_env == "test"


def test_unrecognized_dotenv_key_is_rejected_without_its_value() -> None:
    Path(".env").write_text("APP_ENV=test\nUNRECOGNIZED_KEY=sensitive-invalid-value\n")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "sensitive-invalid-value" not in str(caught.value)


def test_configuration_is_immutable() -> None:
    settings = Settings(app_env="test")
    with pytest.raises(ValidationError, match="frozen"):
        settings.app_env = "production"


def test_database_configuration_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ConfigurationError, match="database_url"):
        create_app()


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///example.db",
        "postgresql://invalid.example/database",
        "postgresql+psycopg://invalid.example/database",
        "postgresql+psycopg://invalid.example/",
        "postgresql+psycopg://invalid.example/database?host=other.example",
    ],
)
def test_database_url_rejects_unsupported_or_incomplete_configuration(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ConfigurationError, match="database_url") as caught:
        load_settings()
    assert url not in str(caught.value)


def test_database_url_is_not_disclosed_in_settings_representation() -> None:
    settings = Settings(app_env="test")
    assert settings.database_url.get_secret_value() not in repr(settings)


def test_database_url_can_be_loaded_from_secret_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    original = load_settings().database_url.get_secret_value()
    path = tmp_path / "database_url"
    path.write_text(original + "\n")
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL_FILE", str(path))
    assert load_settings().database_url.get_secret_value() == original


def test_secret_file_can_be_selected_from_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = Settings(app_env="test").database_url.get_secret_value()
    path = tmp_path / "database_url"
    path.write_text(original)
    Path(".env").write_text(f"APP_ENV=test\nDATABASE_URL_FILE={path}\n")
    monkeypatch.delenv("DATABASE_URL")
    assert load_settings().database_url.get_secret_value() == original


def test_conflicting_database_sources_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL_FILE", "unused")
    with pytest.raises(ConfigurationError, match="Choose"):
        load_settings()


def test_missing_secret_file_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL_FILE", "missing-file")
    with pytest.raises(ConfigurationError, match="Cannot read"):
        load_settings()
