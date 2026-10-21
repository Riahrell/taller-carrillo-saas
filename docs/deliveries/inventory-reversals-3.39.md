# Entrega 3.39 — Corrección trazable y restauración de inventario

## Resultado

«Deshacer último movimiento» permite corregir el último ajuste con diferencia o
entrada/salida manual de un producto activo. Exige identidad confirmada, motivo,
revisión y confirmación. Cantidad y valoración se obtienen del registro original;
no hay campos para enviarlas desde el navegador. Un movimiento posterior, una
reversa previa o un saldo distinto provocan rechazo sin efectos.

Original, compensación y responsable quedan en el historial. Evidencia, libro y
saldo se confirman en una transacción. Reintentar la misma operación conserva la
clave y devuelve su resultado; no compensa dos veces, ni con solicitudes concurrentes.
No cancela recepciones, ventas, órdenes o pagos, ni habilita reversas encadenadas.
Contrato y límites completos: `docs/inventory-reversal-contract.md`.

Los formularios de inventario mantienen ahora el foco local durante edición y
revisión; al cancelar vuelve al botón que abrió la operación. Mensajes y operación
pendiente se muestran junto a la ficha, sin enfocar el encabezado superior.
Catálogo devuelve el foco al botón que abrió el alta/edición. Al regresar de
otra pestaña se restaura el campo enfocado después de revalidar la sesión, solo
si sigue montado dentro del contenido autorizado y el usuario no enfocó otro control.

## Restauración adicional ejecutada

`python3 -m scripts.restore_drill` aprobó dump/restore completo con producto editado,
auditoría, mínimo, conteo, entrada, reversa, saldo y libro conciliado. Se comprobó
la mutación posterior al snapshot y su restauración, además del factor cifrado,
códigos de recuperación y cuarentena. Usa únicamente datos sintéticos y PostgreSQL
temporal protegido por el guard del comando. No valida la copia antigua de la
VPS ni objetos/fotos S3. Evidencia y límites: `docs/inventory-restore-evidence.md`.

## Validación técnica

Desde la raíz del repositorio:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_stock_reversals.py --test-path tests/integration/test_inventory_http.py --test-path tests/integration/test_inventory_schema.py --test-path tests/integration/test_inventory_history.py --maxfail 3
python3 -m scripts.validate_identity --full-backend --maxfail 3 --test-timeout 1800
python3 -m scripts.restore_drill
python3 -m scripts.security
python3 -m unittest discover -s scripts/tests
.venv/bin/ruff check backend scripts/ci.py scripts/validate_identity.py scripts/restore_drill.py
```

Resultados específicos iniciales: 93 pruebas PostgreSQL aprobadas (reversas,
HTTP, esquema e historial). Cubren compensación exacta de entradas/salidas/conteos,
concurrencia de misma/distinta clave, reintento tras desactivación, rol vigente,
rechazo de movimientos posteriores y fallos reales de INSERT/UPDATE con rollback.
La comprobación final incluye además permisos inmutables y bajada protegida.

Corrida completa final: **1942 pruebas backend aprobadas en 988,15 segundos**.
Incluyó comprobación de migraciones sin cambios pendientes y limpieza del entorno
temporal. No se ejecutó el workflow completo de GitHub localmente; los comandos
y resultados de cada validación se detallan aquí.

La primera corrida completa alcanzó el límite local de 600 segundos sin completar.
Se mantuvieron todas las pruebas y se amplió el margen de la corrida completa a
1800 segundos, configurable con valores acotados. CI usa el mismo margen para
backend y 45 minutos totales para el trabajo. No se cambian los criterios de éxito
ni las puertas de seguridad. Las 46 pruebas de herramientas aprobaron.

Auditoría ejecutada: detector y secretos de árbol/historial aprobados, Python locks
sin hallazgos; cuatro avisos npm de herramientas de desarrollo siguen bajo la
excepción existente SEC-2026-001. No se resolvieron ni se agregaron excepciones en
esta entrega. No se afirma ausencia absoluta de vulnerabilidades. El lint global
de scripts detectó una recomendación preexistente en image_security.py; ese archivo
no se cambió. El lint del backend y de los scripts modificados pasó.

```bash
cd /home/riahrell/Documentos/ChatGPT/Saas-inventario-web/frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
"$NODE" node_modules/vitest/vitest.mjs run
"$NODE" node_modules/prettier/bin/prettier.cjs --check src/lib/inventory/api.ts src/components/inventory-manager.tsx tests/inventory-api.test.ts tests/inventory-workspace.test.tsx
```

322 pruebas frontend aprobadas. TypeScript, ESLint y Prettier correctos. Incluye
la conservación de clave/cuerpo ante respuesta perdida, la revisión sin campos
de cantidad/costo, rechazo sin repetición y regresiones de foco/retorno de pestaña.

Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

Imágenes backend/frontend compiladas. Migración local aplicada y revisión 0042
comprobada con Alembic. Backend y frontend saludables. Smoke HTTP aprobado:
HTML español, diez assets, API del mismo origen, 404 de interfaz/API, cabeceras y
rechazo de escritura sin origen/CSRF válidos. Disponible en http://localhost:8080/.

## Revisión manual del propietario

1. Crear una entrada manual en un producto de prueba y anotar el saldo/costo anterior.
   Abrir el historial reciente, pulsar Deshacer último movimiento, introducir motivo,
   revisar y confirmar. El saldo/valor deben regresar al estado anterior y ambos
   registros deben seguir visibles.
2. Registrar dos movimientos: solo se ofrece deshacer el más reciente. Una petición
   antigua o repetida no debe generar otra compensación.
3. Verificar que no se solicitan cantidades ni costos al deshacer. Cancelar no cambia
   nada. Revisar foco, mensajes y botones en móvil y con teclado.
4. Abrir Productos por reponer, actualizar avisos después de los movimientos y comprobar
   que sigue usando el mínimo vigente.

La aceptación visual autenticada continúa pendiente. No se declara cierre para
producción: faltan incorporación/permisos de empleados y operación remota (HTTPS,
restauración de la copia anterior y almacenamiento de objetos). Sin cambios en la
VPS ni mensajes de correo reales como parte de esta entrega.

## Archivos cambiados

- `.github/workflows/ci.yml`
- `backend/app/modules/inventory/history.py`
- `backend/app/modules/inventory/inputs.py`
- `backend/app/modules/inventory/models.py`
- `backend/app/modules/inventory/reversals.py`
- `backend/app/modules/inventory/routes.py`
- `backend/app/modules/inventory/service.py`
- `backend/app/tools/inventory_restore_probe.py`
- `backend/app/tools/restore_probe.py`
- `backend/migrations/versions/0042_stock_reversals.py`
- `backend/tests/integration/test_catalog.py`
- `backend/tests/integration/test_catalog_service.py`
- `backend/tests/integration/test_inventory_http.py`
- `backend/tests/integration/test_inventory_schema.py`
- `backend/tests/integration/test_mfa_consumption.py`
- `backend/tests/integration/test_postgres.py`
- `backend/tests/integration/test_purchase_receipts.py`
- `backend/tests/integration/test_purchase_schema.py`
- `backend/tests/integration/test_stock_minimum.py`
- `backend/tests/integration/test_stock_reversals.py`
- `backend/tests/integration/test_user_provisioning.py`
- `docs/access-current-audit.md`
- `docs/deliveries/inventory-reversals-3.39.md`
- `docs/inventory-history-contract.md`
- `docs/inventory-release-plan.md`
- `docs/inventory-restore-evidence.md`
- `docs/inventory-reversal-contract.md`
- `docs/permissions.md`
- `docs/stock-minimum-contract.md`
- `frontend/src/components/catalog-manager.tsx`
- `frontend/src/components/catalog-workspace.tsx`
- `frontend/src/components/inventory-manager.tsx`
- `frontend/src/components/inventory-workspace.tsx`
- `frontend/src/lib/inventory/api.ts`
- `frontend/tests/catalog-workspace.test.tsx`
- `frontend/tests/inventory-api.test.ts`
- `frontend/tests/inventory-workspace.test.tsx`
- `scripts/ci.py`
- `scripts/restore_drill.py`
- `scripts/validate_identity.py`
