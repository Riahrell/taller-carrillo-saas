"""Owner-scoped teardown of synthetic identity descendants, ephemeral DB only."""

from sqlalchemy import delete, or_, select, text

from app.core.database import Base
from app.modules.access.models import TenantMembership, User


def cleanup_identity(connection, user_ids):
    if connection.scalar(text("SELECT current_database()")) != "carrillo_test":
        raise RuntimeError("Refusing identity cleanup outside test database")
    predicates = {User.__table__: User.id.in_(user_ids)}
    # Metadata order follows foreign keys. Derive descendant scopes before any
    # deletion, then remove leaves first so append-only evidence can be erased
    # exclusively by the isolated migration-owner fixture.
    for table in Base.metadata.sorted_tables:
        if table is User.__table__:
            continue
        terms = []
        for fk in table.foreign_keys:
            parent = fk.column.table
            if parent in predicates:
                terms.append(fk.parent.in_(select(fk.column).where(predicates[parent])))
        if terms:
            predicates[table] = or_(*terms)
    for table in reversed(Base.metadata.sorted_tables):
        if table in predicates and table not in (
            User.__table__,
            TenantMembership.__table__,
        ):
            connection.execute(delete(table).where(predicates[table]))
