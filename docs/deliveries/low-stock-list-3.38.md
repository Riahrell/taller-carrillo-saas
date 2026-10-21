# Entrega 3.38 — Vista global de reposición

Inventario incluye «Ver productos por reponer»: lista privada a demanda con
producto, foto, SKU, existencias y último mínimo. Permite abrir la ficha,
actualizar avisos y paginar. Se excluyen productos sin mínimo, inactivos, de otra
empresa o por encima del mínimo. No genera compras ni envía correos.

Se corrigió también el botón Actualizar consulta: permanece bloqueado durante
la edición de mínimos/fotos y operaciones pendientes.

Contrato y límites: `docs/low-stock-list-contract.md`. Sin migraciones.

## Validación ejecutada

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_low_stock.py --test-path tests/integration/test_inventory_http.py --test-path tests/integration/test_stock_minimum.py --maxfail 3
.venv/bin/ruff check backend
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
"$NODE" node_modules/vitest/vitest.mjs run
```

58 pruebas backend aprobadas en PostgreSQL temporal aislado, 316 frontend
aprobadas, TypeScript/ESLint/Ruff/Prettier correctos. Incluye mínimos históricos
invalidados, saldo inexistente, igualdad, configuración nula, paginación de 27
productos, un producto ajeno con mínimo configurado y cursor fuera de empresa.
La primera preparación de pruebas omitía importar una fixture dependiente; se
corrigió y la ejecución final pasó completa. No se usó la base de desarrollo
para pruebas ni se ejecutó toda la CI en esta entrega.

Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
git diff --check
```

Compilación correcta y actualización local preparada. Sin cambios en la VPS.

## Revisión manual pendiente

Configurar un mínimo igual o superior a las existencias, abrir el panel de
reposición y comprobar que aparece el producto. Abrir su ficha. Desactivar el
mínimo o registrar una entrada que supere el mínimo y pulsar Actualizar avisos:
el producto debe desaparecer. Revisar fotos y distribución en celular.

La consulta no es una instantánea congelada ni se actualiza continuamente. La
validación visual autenticada queda al propietario. Siguiente tarea: reversas
controladas de movimientos de inventario.

## Archivos cambiados

- `backend/app/modules/inventory/low_stock.py`
- `backend/app/modules/inventory/routes.py`
- `backend/app/modules/inventory/service.py`
- `backend/tests/integration/test_inventory_http.py`
- `backend/tests/integration/test_low_stock.py`
- `docs/deliveries/low-stock-list-3.38.md`
- `docs/inventory-release-plan.md`
- `docs/low-stock-list-contract.md`
- `frontend/src/app/globals.css`
- `frontend/src/components/inventory-manager.tsx`
- `frontend/src/components/low-stock-list.tsx`
- `frontend/src/lib/inventory/low-stock.ts`
- `frontend/tests/low-stock.test.tsx`
