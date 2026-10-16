from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.database import DatabaseUnavailable
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_database_engine(monkeypatch: pytest.MonkeyPatch):
    engine = MagicMock()
    monkeypatch.setattr("app.main.build_engine", lambda settings: engine)
    return engine


def test_factory_starts_with_environment_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    with TestClient(create_app()) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/", "/docs", "/redoc", "/openapi.json"])
def test_only_public_liveness_is_exposed_by_default(path: str) -> None:
    with TestClient(create_app(Settings(app_env="production"))) as client:
        assert client.get(path).status_code == 404


def test_local_documentation_requires_explicit_opt_in() -> None:
    with TestClient(
        create_app(Settings(app_env="development", docs_enabled=True))
    ) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/redoc").status_code == 404


def test_liveness_does_not_accept_writes() -> None:
    with TestClient(create_app(Settings(app_env="test"))) as client:
        assert client.post("/health/live").status_code == 405


def test_no_permissive_cors_is_enabled() -> None:
    with TestClient(create_app(Settings(app_env="test"))) as client:
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.status_code == 405
    assert "access-control-allow-origin" not in response.headers


def test_startup_rejects_unavailable_database_without_details(
    isolated_database_engine,
) -> None:
    sensitive_detail = "driver-sensitive-connection-detail"
    connection = isolated_database_engine.connect.return_value.__enter__.return_value
    connection.execute.side_effect = OperationalError(
        "SELECT 1", None, Exception(sensitive_detail)
    )
    with (
        pytest.raises(DatabaseUnavailable) as caught,
        TestClient(create_app(Settings(app_env="test"))),
    ):
        pytest.fail("Application accepted an unavailable database")
    assert sensitive_detail not in str(caught.value)
    isolated_database_engine.dispose.assert_called_once()


def test_engine_is_disposed_after_shutdown(isolated_database_engine) -> None:
    with TestClient(create_app(Settings(app_env="test"))):
        pass
    isolated_database_engine.dispose.assert_called_once()
