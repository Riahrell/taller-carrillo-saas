"""Validated configuration for the features that are actually enabled."""

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
