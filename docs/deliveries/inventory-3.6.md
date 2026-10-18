# Inventario 3.6 — Aritmética de valoración

`backend/app/modules/inventory/valuation.py` incorpora saldo inmutable de cantidad
y valor, recepción con costo explícito y retiro a promedio ponderado. Sigue las
decisiones existentes: cantidad exacta de tres decimales, valoración de seis,
ROUND_HALF_UP, límites NUMERIC y prohibición de stock negativo. El costo total de
salida se redondea una vez; retirar todo consume el valor restante. No se redondea
primero el promedio unitario. El contexto Decimal del llamador no altera resultados.

El costo aplicado se obtiene por diferencia entre valores anterior y posterior;
el futuro servicio deberá persistir esa diferencia junto al movimiento. No hay
mutación de saldos históricos. Un costo cero requiere argumento explícito.

Archivos adicionales: `backend/tests/test_inventory_valuation.py`; contrato de
imágenes `docs/product-images-contract.md`, enlazado desde `docs/data-model.md` y
`docs/catalog-contract.md`. El proveedor confirmado para imágenes es Contabo.

## Validación reproducible desde la raíz

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_inventory_quantities.py backend/tests/test_inventory_valuation.py
.venv/bin/ruff check --config backend/pyproject.toml backend/app/modules/inventory backend/tests/test_inventory_quantities.py backend/tests/test_inventory_valuation.py
.venv/bin/ruff format --check --config backend/pyproject.toml backend/app/modules/inventory backend/tests/test_inventory_quantities.py backend/tests/test_inventory_valuation.py
git diff --check
```

Resultado ejecutado: 48 pruebas aprobadas (30 cantidades y 18 valoración), Ruff
sin incidencias, cinco archivos con formato correcto y diff sin errores. No se
ejecutó CI completo ni pruebas de PostgreSQL: esta entrega no incluye persistencia.

No cambia API, interfaz ni esquema; no necesita migración ni reiniciar servicios.
No habilita todavía stock real: pendientes persistencia atómica, idempotencia,
conteos autorizados, concurrencia PostgreSQL y trazabilidad de cada origen. La
integración real de imágenes también sigue pendiente. La siguiente tarea es el
modelo de conteos, movimientos y saldos con sus restricciones transaccionales.
Las comprobaciones externas de despliegue siguen aplazadas por el propietario.
