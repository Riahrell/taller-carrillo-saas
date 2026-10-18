"""Validated configuration for the features that are actually enabled."""

import re
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(RuntimeError):
    """Startup error that deliberately excludes configuration values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"]
    docs_enabled: bool = False
    database_url: SecretStr
    database_url_file: Path | None = None
    # CLI/migrations need no HTTP key; create_app requires both HTTP fields.
    public_origin: str | None = None
    csrf_signing_key: SecretStr | None = None
    csrf_signing_key_file: Path | None = None
    trusted_proxy_host: str | None = None
    identity_keys_file: Path | None = None

    @field_validator("trusted_proxy_host")
    @classmethod
    def validate_proxy_host(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) > 253
            or not re.fullmatch(r"[a-z][a-z0-9.-]*", value)
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                for part in value.split(".")
            )
        ):
            raise ValueError("A canonical trusted proxy DNS hostname is required")
        return value

    @model_validator(mode="before")
    @classmethod
    def load_database_secret(cls, values: Any) -> Any:
        if not isinstance(values, dict) or not values.get("database_url_file"):
            return values
        if values.get("database_url"):
            raise ConfigurationError("Choose DATABASE_URL or DATABASE_URL_FILE")
        try:
            secret = Path(values["database_url_file"]).read_text().strip()
        except (OSError, UnicodeError):
            raise ConfigurationError(
                "Cannot read database configuration file"
            ) from None
        return {**values, "database_url": SecretStr(secret)}

    @model_validator(mode="before")
    @classmethod
    def load_csrf_secret(cls, values: Any) -> Any:
        if not isinstance(values, dict) or not values.get("csrf_signing_key_file"):
            return values
        if values.get("csrf_signing_key"):
            raise ConfigurationError("Choose CSRF_SIGNING_KEY or CSRF_SIGNING_KEY_FILE")
        try:
            secret = Path(values["csrf_signing_key_file"]).read_text().strip()
        except (OSError, UnicodeError):
            raise ConfigurationError(
                "Cannot read HTTP security configuration file"
            ) from None
        return {**values, "csrf_signing_key": SecretStr(secret)}

    @field_validator("csrf_signing_key")
    @classmethod
    def validate_csrf_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(
            r"[0-9a-f]{64}", value.get_secret_value()
        ):
            raise ValueError(
                "CSRF key requires 32 random bytes encoded as lowercase hex"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            if (
                url.drivername != "postgresql+psycopg"
                or not url.host
                or not url.database
                or not url.username
                or not url.password
                or url.query
            ):
                raise ValueError
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError
        except (ArgumentError, ValueError, TypeError):
            raise ValueError("A complete PostgreSQL psycopg URL is required") from None
        return value

    @model_validator(mode="after")
    def validate_production(self) -> Self:
        if self.app_env == "production" and self.identity_keys_file is None:
            raise ValueError("Production requires external identity keys")
        if self.app_env == "production" and self.docs_enabled:
            raise ValueError("Interactive documentation is forbidden in production")
        return self


def load_settings() -> Settings:
    """Fail before accepting requests, without logging environment values."""
    try:
        return Settings()
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(map(str, error["loc"])) or "configuration"
                for error in exc.errors(include_input=False, include_context=False)
            }
        )
        raise ConfigurationError(
            f"Invalid application configuration: {', '.join(fields)}"
        ) from None
