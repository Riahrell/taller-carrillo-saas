# Inventario 3.19 — Recepción completa y stock atómico

Una compra registrada se recibe una sola vez usando sus cantidades/costos
persistidos. Guarda evidencia, movimientos y saldos en un commit; sin pagos/gastos.

## Archivos

- `purchases/models.py`: recepción, detalles y FK del origen comprado.
- `inventory/models.py`: origen excluyente conteo/recepción en movimientos y
  registro de modelos de compra para resolver FK en el arranque real.
- `inventory/receiving.py`: entrada de stock dentro de la transacción llamadora.
- `purchases/receipts.py`: autorización reciente, idempotencia y recepción completa.
- `migrations/versions/0036_purchase_receipts.py`: ampliación sin alterar saldos,
  permisos inmutables y downgrade protegido.
- `tests/integration/purchase_state.py`: fixture sintética compartida y limpieza.
- `tests/test_model_registration.py`: importación de app en subproceso limpio,
  sin importar fixtures, resolviendo todas las FK.
- Pruebas de recepción nuevas y de registro de compras adaptadas a la fixture;
  revisión actual esperada de pruebas de migración actualizada a 0036.
- Contratos de compras/conteos, modelo, permisos, README y esta entrega.
Todas las rutas de código anteriores son relativas a `backend/`.

## Validación ejecutada

Desde la raíz:

```bash
python3 -m scripts.validate_identity --full-backend --maxfail 3
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
cd backend
../.venv/bin/python -m pytest tests/test_model_registration.py -q
cd ..
```

Backend completo: **1.836 aprobadas**, código 0, migraciones/check correctos y
recursos temporales limpiados. Log
`.local/validation/purchases-3.19-backend-final.log`. Ruff aprobado, 223 archivos
formateados y git diff --check sin errores.
Se conservó además el caso preexistente de importación de acceso junto al nuevo
caso de aplicación; ejecución local final del archivo con ambos: **2 aprobadas**.
No se repitió el backend completo después de conservar ese caso de test previo;
el código runtime final sí corresponde a la imagen de la corrida completa.
Primera corrida completa: 185 aprobadas y tres fallos al revertir por nombre CHECK
prefijado dos veces por Alembic. Se corrigió con op.f, conservando la restricción.
Después: 103 pruebas enfocadas aprobadas (recepción, registro, esquema de stock y
migraciones), más prueba de registro de modelos aislada aprobada. No se atribuye
al intento inicial un resultado completo verde.
Alembic check inicial sin diferencias. Se verifican recepción multiproducto,
promedio ponderado, costo cero, capacidad de cantidad/valor, reintentos históricos,
respuestas perdidas y concurrencia de recepción/conteo. Fallos inyectados al segundo
producto prueban rollback de la primera entrada; FK probadas ante origen/deltas
manipulados y reconciliación de cantidad/valor del libro frente al saldo.

## Límites y siguiente tarea

Sin pantalla/endpoints de compra/recepción; solo servicio interno. No se aplicó
migración a desarrollo/VPS. Sin salida de red, correo o cambios de almacenamiento.
No se repite frontend/CI completa porque no cambian sus fuentes ni dependencias.
El servicio garantiza completitud de recepción, no un CHECK agregado entre tablas.
Runtime no tiene aislamiento RLS frente a SQL arbitrario. El historial de conteos
actual no es todavía un listado de recepciones.

Siguiente: consulta/listado de proveedores y compras, API privada de registro/
recepción e historial de movimientos con origen, antes de conectar la pantalla.
