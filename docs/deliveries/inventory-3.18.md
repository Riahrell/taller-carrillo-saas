# Inventario 3.18 — Registro administrativo de compras

Servicio interno para crear proveedores y registrar/consultar compras de la
empresa autenticada. Recepción y efectos en stock quedan para el siguiente bloque.

## Archivos

- `backend/app/modules/purchases/inputs.py`: nombre de proveedor validado.
- `backend/app/modules/purchases/service.py`: autorización, snapshots, evidencia
  inmutable, totales atómicos, idempotencia y consulta administrativa.
- `backend/tests/integration/test_purchase_service.py`: transacciones reales,
  permisos, referencias, respuestas perdidas, concurrencia y fallos inyectados.
- `backend/tests/test_purchase_calculation.py`: validación de proveedor añadida.
- Contrato de compras, README y esta entrega.

## Comandos ejecutados

Desde la raíz:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_purchase_service.py --test-path tests/integration/test_purchase_schema.py --test-path tests/test_purchase_calculation.py --maxfail 3
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Resultado final: **111 pruebas aprobadas**, código 0. Alembic check correcto,
PostgreSQL temporal eliminado al terminar. Ruff aprobado, 218 archivos formateados
y git diff --check sin errores. Log
`.local/validation/purchases-3.18-validation-final.log`.
La primera corrida detectó SQLSTATE 42501: FOR UPDATE sobre proveedor inmutable
requiere UPDATE. Se retiró ese bloqueo y se conservó el bloqueo de empresa que
serializa operaciones autorizadas; no se concedieron permisos de escritura extra.
Una corrida diagnóstica confirmó el error sin imprimir credenciales o parámetros.

Se cubren cabecera/detalle/autor y costos históricos, precisión monetaria,
proveedor/producto ajeno o inactivo, rol/sesión/contexto, confirmación final,
rollback de inserción/commit, pérdida de respuesta después del commit, misma
clave concurrente, clave conflictiva, claves por empresa y registros incompletos.
No se repitió el backend completo ni frontend/CI: no hay migración, cambio del
guardia compartido, dependencias, rutas o interfaz. Último backend completo 3.17.

## Límites y siguiente tarea

Servicio todavía sin HTTP/UI: no existe pantalla nueva de compras. Alta de
proveedor sin idempotencia general, por lo que no se debe reintentar automáticamente
una respuesta incierta. Las compras sí se recuperan con la misma clave/contenido.
No hay aislamiento RLS contra SQL arbitrario del runtime.
Siguiente: recepción completa, evidencia y movimientos de inventario en una sola
transacción, con idempotencia y pruebas de concurrencia/reconciliación. Después
se conectarán consulta/listado y formularios mediante API privada.
Sin cambios en desarrollo/VPS ni operaciones externas.
