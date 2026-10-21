# Inventario 3.29: entradas y salidas manuales

Nueva tarea del itinerario completada en código: deltas manuales IN/OUT con costo
exacto, motivo, evidencia inmutable, idempotencia y saldo transaccional; pantalla
con revisión y confirmación, e historial manual paginado separado de los conteos.

El contrato completo está en `docs/inventory-manual-contract.md`. La migración
0039 preserva los conteos y recepciones existentes y no admite bajar borrando
movimientos manuales. La UI conserva una operación incierta en memoria ligada a
la sesión; recuperarla repite la misma clave/cuerpo. No persiste credenciales ni
cálculos de saldo final en el navegador.

## Validar desde la raíz

```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
python3 -m scripts.ci
git diff --check
```

Prueba técnica inicial aislada ejecutada: 12 pruebas correctas de transacciones y
migraciones. La validación final de esta entrega se registra más abajo al terminar.
No se usó la base de desarrollo para las pruebas automatizadas ni se enviaron
correos reales.

## Prueba manual del propietario

1. Crear un producto de prueba y abrir Inventario.
2. Registrar entrada de 2 unidades con costo 3 USD/unidad y motivo. Revisar y
   confirmar: cantidad 2, costo promedio 3 y valor de existencias 6 USD.
3. Registrar salida de 1 unidad con motivo: cantidad 1 y valor 3 USD. No pide costo.
4. Intentar otra salida de 2 unidades: debe rechazarla y conservar cantidad 1/valor 3.
5. Consultar Entradas y salidas manuales: solo figuran los dos movimientos guardados.
6. Usar Actualizar existencias únicamente para corregir un conteo físico; su
   historial permanece separado. Confirmar que fotos y precio siguen visibles.

No crear ventas o pagos para probar estos botones. El registro de empleados no
está publicado en esta fase. Faltan edición/estado y stock bajo, historial unificado,
reversas, permisos de empleados y cierre operativo de despliegue. No se declara
inventario terminado ni se ha desplegado en Contabo.


## Resultados finales ejecutados

CI completa con salida 0: `carrillo-ci-1eecc1a7a0bb`.

- 1.886 pruebas backend correctas (290,16 segundos).
- 262 pruebas frontend correctas; 11 pruebas de Nginx correctas.
- 46 pruebas de scripts; workflow, Ruff, formato, tipos, ESLint y builds correctos.
- Smoke HTTP correcto. Sin hallazgos de secretos ni en Python locks.
- Se mantienen cuatro hallazgos npm aceptados por la excepción de desarrollo
  existente; no se agregaron excepciones ni se deshabilitaron verificaciones.
- Cuatro imágenes de runtime con cero hallazgos y evidencia SBOM privada.

Cobertura nueva: valoración ponderada, vaciado sin valor residual, saldo
insuficiente, concurrencia de última unidad, reintento/clave conflictiva, rollback
de commit, aislamiento, revocación, paginación y evidencia inmutable, contrato
HTTP y envío de unidades en lugar de saldo final desde la UI.

Registro: `.local/validation/manual-stock-ci.log`.

Backend/frontend locales reconstruidos y saludables; migración confirmada en
`0039_manual_stock_movements (head)`. Smoke posterior a la actualización correcto
en `http://localhost:8080`. No se crearon productos ni movimientos operacionales.

Comandos locales ejecutados con la configuración privada existente:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate python -m alembic current
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Pendiente de revisión visual por el propietario. Una recarga completa de página
pierde la operación incierta conservada en memoria: revisar el historial antes
de registrar otra vez una operación cuyo resultado se desconoce. No existe
recuperación persistente entre recargas ni reversa automática en esta entrega.

Siguiente tarea del itinerario: historial unificado, seguido por edición/estado
y umbrales de stock, manteniendo los permisos y el cierre operativo pendientes.

## Archivos completos en el repositorio

```text
backend/app/main.py
backend/app/modules/inventory/inputs.py
backend/app/modules/inventory/manual.py
backend/app/modules/inventory/models.py
backend/app/modules/inventory/routes.py
backend/app/modules/inventory/service.py
backend/migrations/versions/0039_manual_stock_movements.py
backend/tests/integration/test_catalog.py
backend/tests/integration/test_catalog_service.py
backend/tests/integration/test_inventory_http.py
backend/tests/integration/test_inventory_schema.py
backend/tests/integration/test_manual_stock.py
backend/tests/integration/test_mfa_consumption.py
backend/tests/integration/test_postgres.py
backend/tests/integration/test_purchase_receipts.py
backend/tests/integration/test_purchase_schema.py
backend/tests/integration/test_user_provisioning.py
docs/deliveries/inventory-manual-3.29.md
docs/inventory-manual-contract.md
docs/inventory-release-plan.md
frontend/src/components/inventory-manager.tsx
frontend/src/components/inventory-workspace.tsx
frontend/src/lib/inventory/api.ts
frontend/tests/inventory-api.test.ts
frontend/tests/inventory-workspace.test.tsx
```
