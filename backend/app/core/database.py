"""Synchronous SQLAlchemy primitives; transaction ownership stays in use cases."""

from sqlalchemy import Connection, Engine, MetaData, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import Settings


class DatabaseUnavailable(RuntimeError):
    """Safe connection error, without driver connection details."""


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
        echo=False,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
    )


def verify_connection(engine: Engine) -> None:
    try:
        with connect_safely(engine) as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise DatabaseUnavailable("Database connection failed") from None


def connect_safely(engine: Engine) -> Connection:
    try:
        return engine.connect()
    except SQLAlchemyError:
        raise DatabaseUnavailable("Database connection failed") from None


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
