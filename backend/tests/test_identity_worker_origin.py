"""Local worker links follow validated environment; production still needs TLS."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.core.config import ConfigurationError
from app.modules.access.identity_delivery import IdentityMailDispatcher


@pytest.mark.parametrize(
    "environment,origin,allowed",
    [
        ("development", "http://localhost:8080", True),
        ("development", "http://127.0.0.1:8080", True),
        ("test", "http://localhost:8080", True),
        ("development", "http://remote.example", False),
        ("production", "http://localhost:8080", False),
        ("production", "https://panel.example.com", True),
        ("typo", "http://localhost:8080", False),
    ],
)
def test_worker_origin_is_scoped_to_explicit_environment(environment, origin, allowed):
    engine = create_engine("sqlite://")
    identity = SimpleNamespace(require=lambda: object())
    try:
        if allowed:
            dispatcher = IdentityMailDispatcher(
                engine,
                identity=identity,
                transport=object(),
                origin=origin,
                environment=environment,
            )
            assert dispatcher.origin == origin
        else:
            with pytest.raises(ConfigurationError):
                IdentityMailDispatcher(
                    engine,
                    identity=identity,
                    transport=object(),
                    origin=origin,
                    environment=environment,
                )
    finally:
        engine.dispose()


def test_worker_defaults_to_production_and_rejects_local_http():
    with pytest.raises(ConfigurationError):
        IdentityMailDispatcher(
            None, identity=None, transport=None, origin="http://localhost:8080"
        )
