# Entrega 3.37 — Mínimo y aviso en la ficha del producto

## Resultado

En Inventario se puede configurar el mínimo de cada producto con confirmación de
identidad, revisión y guardado. Vacío desactiva; cero avisa al agotarse. La ficha
avisa cuando el producto activo tiene existencias menores o iguales al mínimo.
No se asignan valores a productos existentes ni se modifica stock, costo, precio,
fotos o documentos financieros. El listado global por reponer sigue pendiente.

Se almacenan cambios de configuración en evidencia append-only con control de
versión. El conflicto exige recargar. El formulario explica el límite inclusivo,
bloquea otros cambios del producto durante la edición y no reenvía automáticamente
una respuesta incierta. Contrato: `docs/stock-minimum-contract.md`.

## Validación ejecutada

Desde la raíz:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_stock_minimum.py --test-path tests/integration/test_inventory_http.py --test-path tests/integration/test_inventory_schema.py --maxfail 3
.venv/bin/ruff check backend
```

87 pruebas aprobadas en PostgreSQL temporal aislado. Migración/Alembic check
correctos. Cubre límite inclusivo, cero, sin configuración, desactivación del aviso,
producto inactivo, aislamiento, rol vigente, estado intacto del saldo, versión
antigua, concurrencia, fallo de evidencia, permisos append-only y rechazo de una
bajada que borraría historial. Ruff correcto. No se usó la base de desarrollo
para pruebas. No se repitió la batería backend completa tras esta entrega; la
entrega anterior 3.36 sí aprobó sus 1912 pruebas completas.

```bash
cd /home/riahrell/Documentos/ChatGPT/Saas-inventario-web/frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
"$NODE" node_modules/vitest/vitest.mjs run
"$NODE" node_modules/prettier/bin/prettier.cjs --check src/components/stock-minimum.tsx src/lib/inventory/minimum.ts src/components/inventory-manager.tsx src/app/globals.css tests/stock-minimum.test.tsx tests/stock-minimum-api.test.ts tests/inventory-workspace.test.tsx
```

313 pruebas frontend aprobadas; TypeScript, ESLint y Prettier correctos. Verifican
revisión previa, cancelación, cero, conflicto, contexto cambiado, bloqueo de
movimientos durante edición, campos permitidos y rechazo de respuestas incoherentes.

Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

Compilaciones completadas. Actualización local mediante migración 0041 sin
configurar mínimos de productos. Backend y frontend saludables; versión de
esquema confirmada en `0041_stock_minimum (head)` y smoke web correcto. No se agregaron dependencias, secretos, correos,
llamadas al almacenamiento ni cambios en la VPS.

## Pruebas manuales

1. Abrir un producto: debe mostrar «Sin mínimo configurado».
2. Configurar un mínimo igual a las existencias actuales, revisar y guardar: debe
   mostrar «Stock bajo», conservando existencias y costo.
3. Volver a configurar, dejar vacío y guardar: el aviso debe desactivarse.
4. Con un producto sin unidades, configurar 0: debe avisar. Si entran unidades,
   debe desaparecer el aviso al refrescar la ficha.
5. Cancelar un borrador: no debe cambiar la configuración. Revisar en móvil.

## Riesgos y siguiente tarea

Pendiente la revisión visual autenticada del propietario. El aviso se consulta
al abrir la ficha, al guardar su mínimo y al cambiar las existencias; no es una
suscripción en tiempo real. Cambios de otras pestañas pueden requerir recarga.
La evidencia de mínimos no tiene una pantalla propia de consulta en esta entrega.

Siguiente tarea: vista global de productos por reponer. Continúan después las
reversas, permisos/registro de empleados y cierre operativo para VPS.

## Archivos cambiados

- `backend/app/main.py`
- `backend/app/modules/inventory/inputs.py`
- `backend/app/modules/inventory/minimum.py`
- `backend/app/modules/inventory/models.py`
- `backend/app/modules/inventory/routes.py`
- `backend/app/modules/inventory/service.py`
- `backend/migrations/versions/0041_stock_minimum.py`
- `backend/tests/integration/test_catalog.py`
- `backend/tests/integration/test_catalog_service.py`
- `backend/tests/integration/test_inventory_http.py`
- `backend/tests/integration/test_inventory_schema.py`
- `backend/tests/integration/test_mfa_consumption.py`
- `backend/tests/integration/test_postgres.py`
- `backend/tests/integration/test_purchase_receipts.py`
- `backend/tests/integration/test_purchase_schema.py`
- `backend/tests/integration/test_stock_minimum.py`
- `backend/tests/integration/test_user_provisioning.py`
- `docs/deliveries/stock-minimum-3.37.md`
- `docs/inventory-release-plan.md`
- `docs/stock-minimum-contract.md`
- `frontend/src/app/globals.css`
- `frontend/src/components/inventory-manager.tsx`
- `frontend/src/components/stock-minimum.tsx`
- `frontend/src/lib/inventory/minimum.ts`
- `frontend/tests/inventory-workspace.test.tsx`
- `frontend/tests/stock-minimum-api.test.ts`
- `frontend/tests/stock-minimum.test.tsx`
