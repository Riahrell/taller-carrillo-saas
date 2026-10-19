# Inventario 3.17 — Persistencia de proveedores y compras

Esquema inicial con referencias de la misma empresa y evidencia inmutable.
Sin nuevas pantallas/endpoints ni efectos sobre stock, pagos o gastos.

## Archivos

- `backend/app/modules/purchases/models.py`: proveedor, compra y detalle.
- `backend/migrations/versions/0035_purchases.py`: esquema congelado, restricciones,
  índices y revocación de permisos; downgrade rechaza pérdida de datos.
- `backend/migrations/env.py` y conftest de integración: registro de modelos.
- `backend/tests/integration/test_purchase_schema.py`: PostgreSQL real y rol runtime.
- Pruebas de migración existentes de catálogo, stock, MFA y provisionamiento:
  revisión esperada actualizada a 0035; no se relajan sus controles.
- Contrato de compras, modelo, permisos, README y esta entrega.

## Validación ejecutada

Desde la raíz:

```bash
python3 -m scripts.validate_identity --full-backend --maxfail 3
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Backend completo: **1.763 pruebas aprobadas**, código 0. Migraciones, reversión
y Alembic check correctos, recursos temporales eliminados. Log privado
`.local/validation/purchases-3.17-backend.log`. Ruff aprobado, 216 archivos
formateados y git diff --check sin errores.
Pruebas nuevas: relaciones entre empresas, duplicados, costos/valoración/importes
manipulados, estados numéricos no finitos, permisos inmutables, UTC y reversión
protegida incluso con solo un proveedor.

## Límites y siguiente tarea

Total y número de líneas se validarán transaccionalmente en el servicio: no son
un agregado protegido por CHECK SQL. No hay RLS contra SQL arbitrario del runtime.
No se habilitó edición de proveedores ni recepción; no se registraron operaciones
comerciales. Migración aplicada exclusivamente a PostgreSQL efímero de pruebas.
No se repitió frontend/build/CI completa porque no se modificaron sus fuentes
ni dependencias; la validación completa anterior es 3.15.

Siguiente tarea: alta administrativa de proveedores y registro de compras con
autorización, auditoría, idempotencia y rollback; después recepción atómica y UI.
