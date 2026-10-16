"""Operator-only, one-time bootstrap: python -m app.modules.access.bootstrap."""

import argparse
import getpass
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from sqlalchemy import Engine, exists, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import ConfigurationError, load_settings
from app.core.database import DatabaseUnavailable, build_engine, create_session_factory
from app.modules.access.models import MembershipRole, Tenant, TenantMembership, User
from app.modules.access.passwords import hash_password

# Fixed application namespace and operation key, shared by all bootstrap processes.
BOOTSTRAP_LOCK = (1128354386, 1)


class BootstrapUnavailable(Exception):
    """Safe operator error, with no identity or connection values."""


class BootstrapInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    tenant_name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=15, max_length=128)

    @field_validator("tenant_name", "email", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("password")
    @classmethod
    def valid_unicode(cls, value: SecretStr) -> SecretStr:
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise ValueError("Password must be valid Unicode") from None
        return value


@dataclass(frozen=True)
class BootstrapResult:
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID


def bootstrap(engine: Engine, data: BootstrapInput) -> BootstrapResult:
    """Own the complete transaction; refuse existing identities, including inactive.

    The advisory lock coordinates this operator command, not arbitrary SQL writes.
    It is released on commit/rollback. The normal engine uses READ COMMITTED so
    a waiting contender reads the first contender's committed records.
    """
    factory = create_session_factory(engine)
    with factory.begin() as session:
        connection = session.connection()
        expected = ScriptDirectory(
            str(Path(__file__).resolve().parents[3] / "migrations")
        ).get_current_head()
        if MigrationContext.configure(connection).get_current_revision() != expected:
            raise BootstrapUnavailable("Apply the current migrations before bootstrap")
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :operation)"),
            {"namespace": BOOTSTRAP_LOCK[0], "operation": BOOTSTRAP_LOCK[1]},
        )
        if any(
            session.scalar(select(exists().where(model.id.is_not(None))))
            for model in (Tenant, User, TenantMembership)
        ):
            raise BootstrapUnavailable("Bootstrap requires empty access tables")
        encoded = hash_password(data.password)
        tenant = Tenant(name=data.tenant_name)
        user = User(email=str(data.email), password_hash=encoded)
        session.add_all([tenant, user])
        session.flush()
        membership = TenantMembership(
            tenant_id=tenant.id, user_id=user.id, role=MembershipRole.ADMIN
        )
        session.add(membership)
        session.flush()
        result = BootstrapResult(tenant.id, user.id, membership.id)
    # Return only after the commit has succeeded.
    return result


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally repeats unknown arguments, which may contain secrets.
        self.exit(2, "Invalid arguments. Use --help; credentials are prompted only.\n")


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(
        description="Initialize the first tenant and admin once"
    )
    parser.parse_args(argv)
    if not sys.stdin.isatty():
        print(
            "Bootstrap requires an interactive terminal; no piped credentials.",
            file=sys.stderr,
        )
        return 2
    engine = None
    try:
        tenant_name = input("Empresa: ")
        email = input("Correo del administrador: ")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = SecretStr(getpass.getpass("Contraseña (15–128 caracteres): "))
            confirmation = SecretStr(getpass.getpass("Repetir contraseña: "))
        if password != confirmation:
            print("Las contraseñas no coinciden.", file=sys.stderr)
            return 2
        data = BootstrapInput(tenant_name=tenant_name, email=email, password=password)
        engine = build_engine(load_settings())
        bootstrap(engine, data)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print(
            "Bootstrap cancelado; se requiere entrada oculta en terminal.",
            file=sys.stderr,
        )
        return 2
    except ValidationError:
        print(
            "Datos inválidos: revisa empresa, correo y longitud de contraseña.",
            file=sys.stderr,
        )
        return 2
    except BootstrapUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ConfigurationError, DatabaseUnavailable, SQLAlchemyError):
        print(
            "Bootstrap no completado: revisa configuración, migraciones y DB.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # CLI boundary: unexpected exceptions must not expose sensitive locals.
        print(
            "Bootstrap no completado; error interno sin detalles sensibles.",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print("Empresa y administrador creados. El acceso web sigue pendiente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
