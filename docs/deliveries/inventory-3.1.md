# Inventario 3.1 — Cantidades exactas

Primera pieza interna del módulo, conforme a NUMERIC(18,3) definido en
`../decisions.md`. Sin tablas, rutas HTTP ni cambios de existencias todavía.
El usuario pospuso VPS/HTTPS para continuar los módulos en local.

`StockCount` admite un conteo no negativo. `MovementQuantity` exige cantidad
positiva; la futura operación autorizada determina entrada o salida. Ambos
conservan Decimal, rechazan floats/bools/no finitos, desbordamiento y más de tres
decimales, incluso ceros adicionales. No redondean ni dependen de la precisión
decimal global del proceso. Los futuros clientes JSON deben enviar fracciones
como strings para conservar su representación exacta.

Archivos de aplicación: `backend/app/modules/inventory/__init__.py`,
`backend/app/modules/inventory/quantities.py`; regresiones en
`backend/tests/test_inventory_quantities.py`.

Validación ejecutada desde la raíz:

```bash
.venv/bin/ruff format --config backend/pyproject.toml backend/app/modules/inventory backend/tests/test_inventory_quantities.py
.venv/bin/ruff check --config backend/pyproject.toml backend/app/modules/inventory backend/tests/test_inventory_quantities.py
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_inventory_quantities.py -q
git diff --check
```

Resultado: Ruff sin errores y **30 pruebas pasan**. Son pruebas de validación
interna, no demuestran stock transaccional, aislamiento ni integración PostgreSQL.
No se ejecutó de nuevo CI integral ni se arrancó/desplegó la aplicación.

Siguiente tarea: cerrar campos mínimos de catálogo e incorporar persistencia de
productos/categorías con unicidad de SKU por empresa y FK compuestas. Las
operaciones de stock requerirán libro/saldo, autorización, idempotencia,
valoración y pruebas concurrentes en PostgreSQL antes de habilitar escritura.
VPS/TLS y restauración del sistema anterior permanecen pendientes.
