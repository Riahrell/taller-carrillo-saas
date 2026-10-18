# Inventario 3.7 — Conteo y valoración de diferencias

Implementado `StockValue.recount` en
`backend/app/modules/inventory/valuation.py` y entrada estricta `PhysicalCount`
en `backend/app/modules/inventory/inputs.py`. Pruebas nuevas en
`backend/tests/test_inventory_counts.py`. El contrato
`docs/inventory-count-contract.md` define la persistencia siguiente y distingue
explícitamente lo implementado del diseño pendiente.

Cantidad final no negativa, costo histórico en diferencias, costo explícito si
falta valoración utilizable, ausencia de revaluación por conteo igual, rechazo de
campos de autoridad suministrados por cliente y precisión independiente del
contexto Decimal. No afecta precio de catálogo ni integra Object Storage.

## Comandos desde la raíz

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_inventory_counts.py backend/tests/test_inventory_valuation.py backend/tests/test_inventory_quantities.py
.venv/bin/ruff check --config backend/pyproject.toml backend/app/modules/inventory backend/tests/test_inventory_counts.py backend/tests/test_inventory_valuation.py backend/tests/test_inventory_quantities.py
.venv/bin/ruff format --check --config backend/pyproject.toml backend/app/modules/inventory backend/tests/test_inventory_counts.py backend/tests/test_inventory_valuation.py backend/tests/test_inventory_quantities.py
git diff --check
```

Resultado: 79 pruebas aprobadas (31 de conteos, 18 de valoración y 30 de cantidades).
Sin migración ni ruta nueva; no hace falta reiniciar servicios. No se ejecutaron
pruebas PostgreSQL ni CI completo en esta entrega porque todavía no hay cambios
de persistencia. El stock real sigue sin habilitar. Siguiente tarea: migración de
conteos, movimientos y saldos y sus pruebas PostgreSQL, según el contrato enlazado.
