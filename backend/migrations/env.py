"""Alembic environment, shared configuration and optional caller connection."""

from alembic import context

from app.core.config import load_settings
from app.core.database import Base, build_engine, connect_safely
from app.modules.access import models  # noqa: F401 -- register mapped tables
from app.modules.catalog import models as catalog_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def run_with_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    settings = load_settings()
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    run_with_connection(connection)
else:
    engine = build_engine(load_settings())
    try:
        with connect_safely(engine) as connection:
            run_with_connection(connection)
    finally:
        engine.dispose()
