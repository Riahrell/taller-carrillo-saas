# Inventario 3.30: buscador de productos

Se agrega búsqueda privada por nombre o SKU en Inventario y Catálogo.
Busca coincidencias parciales en el servidor, sin distinguir mayúsculas, sobre
los productos de la empresa resuelta por la sesión. No filtra solo los 25
productos ya cargados ni consulta empresas ajenas.

## Comportamiento y contrato

`GET /catalog/products?limit=25&q=<texto>&after=<UUID>` mantiene la respuesta
existente (`items`, `next_after`). El término es opcional, se recorta y limita a
200 caracteres; vacío muestra la lista normal. La paginación conserva el filtro
actual; una búsqueda nueva o limpiar el texto comienza en la primera página.
Las categorías no aceptan este parámetro.

SQLAlchemy enlaza los parámetros de búsqueda. `%`, `_` y la barra inversa se
tratan como caracteres literales; no son comodines para ampliar la búsqueda.
Se conservan los controles de sesión/empresa/rol, límites de página, timeout de
transacción, cabeceras privadas y rechazo de parámetros adicionales o duplicados.
La búsqueda respeta las tildes y no ofrece búsqueda aproximada ni ranking.

La UI usa etiqueta accesible, campo de búsqueda, envío con Enter o Buscar y
Limpiar búsqueda. Deshabilita la consulta mientras se edita, revisa o resuelve un
movimiento/imagen. Al no encontrar resultados en Inventario retira la ficha
anterior para no actuar sobre un producto distinto del resultado buscado.

## Comandos ejecutados y resultados

Desde la raíz:

```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
python3 -m scripts.validate_identity --test-path tests/test_catalog_inputs.py --test-path tests/integration/test_catalog_http.py --test-path tests/integration/test_catalog_service.py --maxfail 3
git diff --check
```

Con Node 24 desde frontend:

```bash
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/vitest/vitest.mjs run
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
```

Resultados reales: 113 pruebas backend del área afectada correctas en PostgreSQL
desechable, 267 pruebas frontend correctas en 18 archivos. Ruff, formato,
TypeScript y ESLint correctos. Builds backend/frontend correctos. No se repitió
la batería completa de CI para este cambio de lectura; la validación backend se
centró en catálogo/búsqueda y la validación frontend abarcó toda su batería.

Registros privados locales: `.local/validation/product-search-backend.log`,
`product-search-frontend.log` y `product-search-development-build.log`.

## Prueba manual

1. Abrir Inventario; buscar parte de un nombre y pulsar Buscar o Enter.
2. Buscar parte del SKU, también en minúsculas si el código está en mayúsculas.
3. Buscar un texto que no exista: mensaje sin resultados y sin ficha anterior.
4. Limpiar búsqueda: recuperar el listado normal.
5. Con suficientes coincidencias, Más productos conserva el término buscado.
6. Repetir la búsqueda en Catálogo y abrir la ficha de un resultado.

La validación visual queda a cargo del propietario. No se escribieron productos
ni existencias operacionales y no se envió correo real. No requiere migración.
El aislamiento y la paginación de 26 coincidencias se comprobaron en pruebas;
optimizar búsquedas de gran volumen queda sujeto a medición con ese volumen.

Siguiente tarea: historial unificado de inventario. Siguen pendientes edición y
estado de productos, stock bajo, reversas, empleados/permisos y cierre para VPS.
Esta entrega no declara el inventario terminado ni despliega en Contabo.


## Entorno local actualizado

Backend y frontend saludables después de la actualización; smoke HTTP sobre
`http://localhost:8080` correcto. La revisión visual autenticada del buscador
sigue en la guía manual anterior.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

## Archivos completos en el repositorio

```text
backend/app/modules/catalog/inputs.py
backend/app/modules/catalog/routes.py
backend/app/modules/catalog/service.py
backend/tests/integration/test_catalog_http.py
backend/tests/test_catalog_inputs.py
docs/deliveries/product-search-3.30.md
docs/inventory-release-plan.md
frontend/src/app/globals.css
frontend/src/components/catalog-manager.tsx
frontend/src/components/inventory-manager.tsx
frontend/src/components/product-search.tsx
frontend/src/lib/catalog/api.ts
frontend/tests/catalog-api.test.ts
frontend/tests/catalog-workspace.test.tsx
frontend/tests/inventory-workspace.test.tsx
```
