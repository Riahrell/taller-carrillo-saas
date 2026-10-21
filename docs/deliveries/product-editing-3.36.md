# Entrega 3.36 — Edición y estado del producto

## Resultado

Catálogo ofrece «Editar producto» en cada ficha. Un administrador puede cambiar
nombre, SKU, categoría, precio de venta y estado, revisar y guardar. Desactivar
conserva stock, costo, fotos e historial. Los botones de edición e inventario
quedan agrupados y el catálogo conserva la lista durante el formulario.

La versión revisada se compara bajo bloqueo antes de modificar el producto.
Un conflicto exige recargar expresamente; no se sobrescriben cambios ajenos.
El servicio guarda evidencia inmutable antes/después en la misma transacción,
revalida autorización y no escribe en las tablas de inventario o finanzas.
Contrato: `docs/product-editing-contract.md`.

Migración 0040 agrega revisión interna y `product_changes`. La bajada rechaza
eliminar evidencia o revisiones ya utilizadas. Se actualizaron las expectativas
de versión final de los tests de migraciones existentes.

## Validación ejecutada

Desde la raíz del repositorio:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_product_editing.py --test-path tests/integration/test_catalog_http.py --maxfail 3
python3 -m scripts.validate_identity --full-backend --maxfail 3
.venv/bin/ruff check backend
```

Resultados: 38 pruebas específicas aprobadas; batería backend completa de 1912
aprobadas en PostgreSQL temporal aislado (308,04 segundos). Migraciones y Alembic
check correctos; Ruff correcto. Cubre auditoría atómica, rollback, SKU ocupado,
concurrencia, versión antigua y A→B→A, permisos, referencias ajenas, conservación
de stock con saldo real, estado e inmutabilidad de evidencia. No se usó la base
de desarrollo para ejecutar estas pruebas.

```bash
cd /home/riahrell/Documentos/ChatGPT/Saas-inventario-web/frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
"$NODE" node_modules/vitest/vitest.mjs run
"$NODE" node_modules/prettier/bin/prettier.cjs --check src/components/product-editor.tsx src/components/catalog-manager.tsx src/lib/catalog/api.ts src/app/globals.css tests/product-editor.test.tsx tests/catalog-api.test.ts tests/catalog-workspace.test.tsx tests/access-panel.test.tsx tests/identity-confirmation.test.tsx
```

Resultado final: 299 pruebas frontend aprobadas; TypeScript, ESLint y Prettier
correctos. Se verifican edición, revisión previa, cancelación, conflictos,
contexto cambiado, respuesta tardía y petición limitada a campos permitidos.
Durante la validación se corrigieron expectativas de accesibilidad del botón y
el orden de preparación de un test. Las pruebas existentes de foco ahora esperan
el efecto de React con `waitFor`, manteniendo la exigencia de foco; antes podían
fallar por comprobarlo apenas aparecía el elemento. No se cambió la seguridad de
la aplicación para hacerlas pasar.

Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm --no-deps migrate python -m alembic current
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

Compilaciones completadas. Migración local confirmada en `0040_product_changes
(head)`. Backend y frontend saludables. Smoke web correcto: HTML, recursos, API,
cabeceras y rechazo de escrituras sin Origin/CSRF válidos. No hubo cambios de
paquetes ni llamadas a correo/S3 para esta entrega. No se ejecutó la CI completa
ni validación visual de una sesión autenticada. No se modificó la VPS.

## Pruebas manuales del propietario

1. Recargar la web y abrir Catálogo → Editar producto. Cambiar nombre, SKU,
   categoría o precio; revisar y guardar. Confirmar que existencias y costo
   siguen iguales en Inventario.
2. Desactivar un producto, verificar que se conserva en el listado y que no
   permite nuevos movimientos. Reactivarlo y comprobar foto e historial.
3. Editar y cancelar: el producto debe conservar sus datos anteriores.
4. Abrir el mismo producto en dos pestañas. Guardar en una y luego intentar
   guardar el borrador de la otra: debe pedir recargar, sin sobrescribir.
5. Revisar alineación y campos en móvil y con teclado.

## Límites y siguiente tarea

La evidencia de cambios del catálogo se almacena; no se agregó una pantalla de
consulta de esa auditoría. Una respuesta perdida exige consultar el producto
antes de repetir: no hay reintentos automáticos de PATCH. Sigue pendiente la
revisión visual del propietario y el cierre del inventario para VPS (stock bajo,
reversas, permisos de empleados y operación/respaldos/HTTPS).

Siguiente tarea: configurar el mínimo de stock por producto, sin asignar valores
arbitrarios a productos existentes.

## Archivos cambiados

- `backend/app/main.py`
- `backend/app/modules/catalog/editing.py`
- `backend/app/modules/catalog/inputs.py`
- `backend/app/modules/catalog/models.py`
- `backend/app/modules/catalog/routes.py`
- `backend/app/modules/catalog/service.py`
- `backend/migrations/versions/0040_product_changes.py`
- `backend/tests/integration/test_catalog.py`
- `backend/tests/integration/test_catalog_http.py`
- `backend/tests/integration/test_catalog_service.py`
- `backend/tests/integration/test_inventory_schema.py`
- `backend/tests/integration/test_mfa_consumption.py`
- `backend/tests/integration/test_postgres.py`
- `backend/tests/integration/test_product_editing.py`
- `backend/tests/integration/test_purchase_receipts.py`
- `backend/tests/integration/test_purchase_schema.py`
- `backend/tests/integration/test_user_provisioning.py`
- `docs/deliveries/product-editing-3.36.md`
- `docs/inventory-release-plan.md`
- `docs/product-editing-contract.md`
- `frontend/src/app/globals.css`
- `frontend/src/components/catalog-manager.tsx`
- `frontend/src/components/product-editor.tsx`
- `frontend/src/lib/catalog/api.ts`
- `frontend/tests/access-panel.test.tsx`
- `frontend/tests/catalog-api.test.ts`
- `frontend/tests/catalog-workspace.test.tsx`
- `frontend/tests/identity-confirmation.test.tsx`
- `frontend/tests/product-editor.test.tsx`
