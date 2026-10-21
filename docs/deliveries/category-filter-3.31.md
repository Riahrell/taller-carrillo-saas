# Inventario 3.31: filtro por categoría

Inventario y Catálogo permiten combinar nombre/SKU y categoría. El selector se
aplica al cambiar la categoría y comienza en la primera página; la paginación
conserva ambos filtros. Todas las categorías mantiene solo el texto. Limpiar
búsqueda elimina ambos. No modifica productos, precio, existencias o permisos.

## Contrato

`GET /catalog/products?category_id=<UUID>&q=<texto>&after=<UUID>&limit=25`.
Categoría es un filtro opcional, no autoridad para elegir empresa. La empresa se
resuelve desde la sesión. Categorías ajenas y desconocidas devuelven la misma
página vacía; un UUID mal formado o parámetros duplicados se rechazan.

El selector carga las categorías propias, incluidas las páginas posteriores a
las primeras 25, y las muestra ordenadas por nombre. Las inactivas se identifican.
Las respuestas tardías de otra membresía se descartan. Hay cancelación y límite
de tiempo total; si falla la carga se permite reintentar y buscar por texto sin
borrar silenciosamente el filtro de categoría ya aplicado. La API frontend
rechaza productos que no pertenezcan a la categoría solicitada.

## Validaciones ejecutadas

- 115 pruebas backend correctas en PostgreSQL desechable (11,92 segundos).
- 274 pruebas frontend correctas en 19 archivos.
- TypeScript, ESLint, Ruff, formato y diff sin errores.
- Se prueban filtro combinado, paginación con 26 coincidencias, aislamiento por
  empresa, categorías ajenas/desconocidas, selección posterior a la primera
  página, respuestas tardías y reintento de la carga.

La validación backend se limita al área afectada; no se repitió la CI completa.
No se usó la base de desarrollo para pruebas automatizadas ni se envió correo.
No requiere migración. La validación visual autenticada queda al propietario.

Desde la raíz:

```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
python3 -m scripts.validate_identity --test-path tests/test_catalog_inputs.py --test-path tests/integration/test_catalog_http.py --test-path tests/integration/test_catalog_service.py --maxfail 3
git diff --check
```

Desde frontend con Node 24:

```bash
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/vitest/vitest.mjs run
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
```

## Prueba manual

1. Abrir Inventario y seleccionar una categoría: solo aparecen sus productos.
2. Escribir parte del nombre/SKU y pulsar Buscar: conserva esa categoría.
3. Si hay más resultados, cambiar de página: conserva texto y categoría.
4. Elegir Todas las categorías: conserva el texto, elimina la restricción de categoría.
5. Limpiar búsqueda: recupera todos los productos de la empresa.
6. Repetir en Catálogo. Un filtro sin coincidencias retira la ficha anterior.

Categorías aún no creadas se administran en Catálogo, apartado Categorías. Este
selector no crea categorías ni permite consultar las de otras empresas.

Siguiente tarea: historial unificado. Siguen pendientes edición/estado, stock
bajo, reversas, empleados/permisos y cierre operativo para VPS. Esta entrega no
termina el módulo completo ni despliega en Contabo.


## Actualización local

Builds correctos; backend y frontend saludables después de actualizar.
Smoke HTTP correcto en `http://localhost:8080`. No se cambió el esquema ni se
aplicaron migraciones. Registros privados: `.local/validation/category-filter-backend.log`,
`category-filter-frontend.log`, `category-filter-development-build.log` y
`category-filter-development-smoke.log`.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

## Archivos completos en el repositorio

```text
backend/app/modules/catalog/inputs.py
backend/app/modules/catalog/service.py
backend/tests/integration/test_catalog_http.py
backend/tests/test_catalog_inputs.py
docs/catalog-contract.md
docs/catalog-http-contract.md
docs/deliveries/category-filter-3.31.md
docs/inventory-release-plan.md
frontend/src/app/globals.css
frontend/src/components/catalog-manager.tsx
frontend/src/components/inventory-manager.tsx
frontend/src/components/product-search.tsx
frontend/src/lib/catalog/api.ts
frontend/tests/catalog-api.test.ts
frontend/tests/catalog-workspace.test.tsx
frontend/tests/inventory-workspace.test.tsx
frontend/tests/product-search.test.tsx
```
