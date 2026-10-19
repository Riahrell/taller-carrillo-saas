# Inventario 3.16 — Base de compras

Se inicia compras con entrada tipada y cálculo exacto, reutilizando la valoración
de inventario. No se añaden pantalla, endpoints, tablas ni operaciones de stock.

## Archivos

- `backend/app/modules/purchases/{__init__,inputs,calculation}.py`: entrada y cálculo.
- `backend/app/modules/inventory/costs.py`, `inputs.py`: validador compartido de
  costo unitario; conteos conservan precisión y costo opcional existentes.
- `backend/tests/test_purchase_calculation.py`: casos financieros y manipulación.
- Contrato de compras, README y esta entrega.

## Validación

Desde la raíz, salvo el primer comando que entra a backend:

```bash
cd backend
../.venv/bin/python -m pytest tests/test_purchase_calculation.py tests/test_inventory_counts.py tests/test_inventory_valuation.py tests/test_inventory_quantities.py -q
cd ..
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_service.py --maxfail 3
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

111 pruebas unitarias aprobadas. Se verificaron cantidades fraccionarias,
costo cero explícito, límites, redondeo por línea, doble redondeo, contexto Decimal
hostil, copias sin validación y campos de autoridad/total manipulados.
Integración PostgreSQL: 30 aprobadas, migraciones y alembic check correctos,
código 0 y recursos temporales limpiados. Log
`.local/validation/purchases-3.16-integration.log`. Ruff aprobado, 213 archivos
formateados y git diff --check sin errores.
No se repitieron frontend, build ni CI completa porque este bloque no modifica
sus fuentes, dependencias o configuración. La última CI completa corresponde a 3.15.

## Límites y siguiente tarea

No está operativo todavía el registro de compras: falta persistencia de
proveedores/compras, recepción transaccional/idempotente y después HTTP/interfaz.
La siguiente tarea es ese modelo persistente, con restricciones entre empresas.
No se aplicaron migraciones a desarrollo/VPS ni se enviaron correos o archivos.
