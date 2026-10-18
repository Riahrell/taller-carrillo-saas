# Inventario 3.8 — Persistencia del libro de conteos

Se añaden `stock_balances`, `stock_counts` y `stock_movements` mediante
`0032_stock_counts`. Saldo único empresa/producto; cantidades y valores acotados,
sin negativos ni valor retenido con cantidad cero. Conteo con motivo, actor de
la empresa, snapshots y clave de operación única por empresa. Movimiento con
origen referencial y diferencias idénticas al conteo, sin duplicación ni cantidad
cero. El rol runtime no puede reescribir o borrar evidencia ni eliminar saldos.
Downgrade se niega si hay datos en cualquiera de las tres tablas.

## Archivos

- `backend/app/modules/inventory/models.py`: esquema ORM.
- `backend/migrations/versions/0032_stock_counts.py`: esquema físico y permisos.
- `backend/app/modules/access/models.py`, `backend/migrations/env.py`: registro
  de modelos, sin cambiar comportamiento de acceso.
- `backend/tests/integration/test_inventory_schema.py`: restricciones, FK,
  unicidad, permisos y reversión en PostgreSQL real.
- Tests existentes `test_catalog.py`, `test_catalog_service.py`,
  `test_mfa_consumption.py`, `test_postgres.py`, `test_user_provisioning.py`:
  expectativa de revisión head actualizada; no se debilitan sus guardias.
- `docs/inventory-count-contract.md`, `docs/data-model.md`, README y esta entrega.

## Comandos desde la raíz

```bash
python3 -m scripts.validate_identity --full-backend
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_schema.py
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

El runner construye y migra una base desechable y ejecuta `alembic check` antes de
las pruebas. No aplicar todavía estas migraciones a desarrollo o VPS. Sin cambio
de interfaz o rutas, no requiere reiniciar servicios para probar la web existente.

Resultados reales: batería completa del backend, 1.532 pruebas aprobadas en
158,67 segundos. Después, 32 pruebas del esquema final aprobadas en 0,97 segundos
(incluye dos pruebas añadidas tras construir la imagen de la batería completa).
Ambas ejecuciones terminaron con código 0, migración y `alembic check` correctos,
y limpieza de sus recursos. Logs locales:
`.local/validation/inventory-3.8-backend.log` y
`.local/validation/inventory-3.8-schema.log`. Ruff sin incidencias, 187 archivos
con formato correcto y `git diff --check` sin errores. No se repitieron frontend,
QA de navegador ni escaneo de imágenes; no hay cambios en esos componentes.
El primer intento sin permisos Docker falló antes de ejecutar pruebas; se repitió
con acceso autorizado al daemon, sin sustituir ese fallo por un resultado verde.

## Límites y siguiente tarea

El esquema no autoriza un conteo por sí solo y aún no actualiza stock desde HTTP.
Faltan el servicio transaccional, bloqueo del primer saldo, repetición idempotente,
reconciliación y pruebas de concurrencia/rollback. La clave única no sustituye la
comparación de huella ni autorización en reintentos. No hay RLS frente a SQL
arbitrario del runtime. El servicio deberá mantener juntos evidencia, movimiento
y saldo, y no inventar movimiento para un conteo igual. Implementarlo es la
siguiente tarea antes de habilitar cualquier formulario de existencias.
