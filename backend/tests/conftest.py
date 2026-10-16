import secrets

import pytest
from sqlalchemy.engine import URL


@pytest.fixture(autouse=True)
def isolate_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path, request) -> None:
    """Never load the developer's .env or inherit application settings."""
    if request.node.get_closest_marker("integration"):
        return
    monkeypatch.chdir(tmp_path)
    for key in (
        "APP_ENV",
        "DOCS_ENABLED",
        "app_env",
        "docs_enabled",
        "DATABASE_URL",
        "DATABASE_URL_FILE",
        "database_url",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        URL.create(
            "postgresql+psycopg",
            username="unit_test",
            password=secrets.token_urlsafe(24),
            host="invalid.example",
            database="unit_test",
        ).render_as_string(hide_password=False),
    )
