# Entrega 3.35 — Historial unificado y estabilidad del catálogo

## Resultado

Inventario muestra un historial por producto: ajustes (incluso sin diferencia),
entradas/salidas manuales y recepciones de compras existentes. Cada registro
muestra fecha en El Salvador, responsable, motivo cuando existe, existencias
anteriores/actuales, diferencias y una referencia desplegable del origen.
Paginación conjunta de 25 registros, del más reciente al más antiguo.

En Catálogo se conserva la lista durante las consultas y al abrir formularios.
Permanece inerte durante carga/edición. Antes se vaciaba o desmontaba, acortando
la página y pudiendo desplazarla al inicio. Se desactiva el anclaje automático de
contenido en ambos gestores. Las comprobaciones de sesión y contexto permanecen.

La consulta nueva no cambia inventario ni requiere migraciones. Se conservan los
endpoints anteriores por compatibilidad. Contrato completo en
`docs/inventory-history-contract.md`.

## Archivos

- Backend: nuevo `backend/app/modules/inventory/history.py`; `inputs.py`,
  `routes.py`, `service.py` del mismo módulo.
- Pruebas backend: nuevo `backend/tests/integration/test_inventory_history.py`;
  `backend/tests/integration/test_inventory_http.py`.
- Frontend: `frontend/src/components/catalog-manager.tsx`, `inventory-manager.tsx`;
  `frontend/src/lib/inventory/api.ts`; `frontend/src/app/globals.css`.
- Pruebas frontend: `frontend/tests/catalog-workspace.test.tsx`,
  `inventory-api.test.ts`, `inventory-data.ts`, `inventory-workspace.test.tsx`.
- Documentación: contrato, `docs/inventory-release-plan.md` y este documento.

## Comandos y resultados reales

Desde la raíz del repositorio:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_history.py --test-path tests/integration/test_inventory_http.py --maxfail 3
.venv/bin/ruff check backend/app/modules/inventory backend/tests/integration/test_inventory_history.py backend/tests/integration/test_inventory_http.py
```

47 pruebas backend aprobadas en PostgreSQL temporal aislado. Alembic no detectó
operaciones nuevas. Cubre fuentes mezcladas, conteos sin diferencia, recepción,
lecturas sin modificar saldo, orden/cursor, inserciones entre páginas, permisos,
sesión revocada, aislamiento, campos HTTP y rechazo de parámetros inválidos.
Ruff correcto. No se utilizó la base de desarrollo para las pruebas.

```bash
cd /home/riahrell/Documentos/ChatGPT/Saas-inventario-web/frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
"$NODE" node_modules/vitest/vitest.mjs run
"$NODE" node_modules/prettier/bin/prettier.cjs --check src/components/catalog-manager.tsx src/components/inventory-manager.tsx src/lib/inventory/api.ts src/app/globals.css tests/inventory-api.test.ts tests/inventory-workspace.test.tsx tests/inventory-data.ts tests/catalog-workspace.test.tsx
```

288 pruebas frontend aprobadas; TypeScript, ESLint y Prettier correctos. Se prueba
la conservación de la lista, el historial único, su paginación y la validación de
respuestas: tipos de origen, empate de fecha/UUID entre tablas, campos ajenos,
diferencias incoherentes, cursor, frontera y producto. No se agregaron dependencias.

Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

Compilaciones completadas, servicios locales saludables y smoke web correcto:
HTML español, recursos, API del mismo origen, cabeceras y rechazo de escrituras
sin origen/CSRF válido. No se ejecutó toda la CI ni pruebas visuales autenticadas.

## Pruebas manuales del propietario

1. Recargar la web. En Catálogo, bajar en la lista, pulsar paginación/búsqueda,
   abrir Nuevo producto y cancelar. Comprobar que no salta al inicio durante la
   carga. Cambiar de pestaña y regresar: debe conservar el contenido si la sesión
   y empresa siguen siendo las mismas.
2. En Inventario, abrir un producto con movimientos existentes. Debe haber un
   único «Historial de inventario», con responsable y tipo de cada registro.
3. Expandir la referencia del origen y revisar el diseño en móvil.
4. En un producto con más de 25 registros, usar Movimientos anteriores y volver
   a Movimientos recientes. No deben repetirse registros entre páginas estables.

## Límites y siguiente tarea

El correo del responsable se consulta de su cuenta actual; no es una captura de
su correo histórico. La referencia de compra es real, pero no se creó una nueva
pantalla de compras. No se implementaron ventas, órdenes ni reversas. La paginación
no congela una instantánea completa: nuevas filas recientes aparecen al volver
al principio. Si el resultado nuevo es mucho más corto, el navegador puede ajustar
el desplazamiento al nuevo final de página; esto requiere validación visual real,
especialmente en móvil. Los errores de fotos no se consideran resueltos por este
ajuste. Sigue pendiente el cierre operativo para despliegue.

Siguiente tarea: editar productos y activar/desactivar su disponibilidad, sin
permitir modificar stock desde el formulario del catálogo.
