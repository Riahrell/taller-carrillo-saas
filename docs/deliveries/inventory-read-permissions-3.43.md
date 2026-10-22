# 3.43 — Consulta de inventario autorizada por el administrador

El administrador concede o retira consulta desde `/permisos` a empleados activos con perfil Solo lectura. El empleado entra en `/inventario/consulta`: productos activos, fotos privadas, búsqueda, categoría, existencias y precio de venta. La respuesta del servidor no incluye costos ni valoración. No permite modificar inventario.

## Archivos cambiados

- Backend: nuevos `access/inventory_permission_models.py`, `inventory_permissions.py`, `inventory_permission_routes.py`, `inventory/availability.py` y migración `0045_inventory_read_grants.py`; integración en main, modelos, administración, rutas e imágenes.
- Frontend: rutas `/permisos` y `/inventario/consulta`, componentes de consulta y administración, clientes tipados de permisos/disponibilidad; adaptación de buscador, miniaturas, navegación y estilos.
- Pruebas: integración de permisos, actualización de expectativas de migración y dos archivos nuevos de Vitest.
- Documentación: contrato de consulta, política de registro, matriz de permisos y plan de entrega de inventario.

## Validación ejecutada

Desde la raíz:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_permissions.py --test-path tests/integration/test_product_image_service.py --test-path tests/integration/test_product_image_http.py --test-path tests/integration/test_membership_administration.py --maxfail 3
python3 -m scripts.security
git diff --check
```

Resultado: 83 pruebas de backend aprobadas; migraciones sin operaciones pendientes. Incluyen aislamiento entre empresas, denegación por defecto, revocación con sesión abierta, concurrencia con ETag, rollback, evidencia inmutable y control antes/después de descargar fotos. Desactivar o cambiar el perfil retira la concesión sin restaurarla al reactivar.

Desde `frontend`, con Node 24 en PATH:

```bash
npm run typecheck
npm run lint
npm test
npx prettier --check tests/inventory-availability-api.test.ts
npx vitest run tests/inventory-permissions.test.tsx tests/inventory-availability-api.test.ts
```

Resultado: tipos y ESLint aprobados; suite completa 336 pruebas aprobadas en 26 archivos. Revalidación final de los dos archivos nuevos: 8 aprobadas (incluidas en las 336, no adicionales). Build de backend y frontend aprobado; frontend reconstruido después del ajuste final de estilos.

Configuración local utilizada:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend proxy
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Migración local confirmada: `0045_inventory_read_grants (head)`. Los registros completos quedan ignorados en `.local/validation/inventory-permissions-*`.

## Prueba manual del usuario

1. Como administrador, abre `/permisos`, selecciona un empleado aprobado con perfil Solo lectura, concede consulta, revisa y confirma.
2. Como empleado, abre Consultar existencias. Comprueba búsqueda, categorías, foto, precio y cantidad; no debe mostrar costos ni acciones de edición.
3. Retira el permiso como administrador. Al actualizar o volver a la pestaña, la consulta debe bloquearse y retirar los datos.
4. Desactiva y reactiva al empleado: la concesión anterior no debe recuperarse automáticamente.

## Límites y siguiente tarea

No se ejecutó una prueba manual autenticada en navegador ni se desplegó en VPS. La revocación bloquea la siguiente operación del servidor; no puede borrar información ya observada o descargada. La interfaz revalida al recuperar foco o actualizar.

El escaneo no encontró secretos ni vulnerabilidades en locks Python; conserva cuatro hallazgos npm de herramientas de desarrollo bajo la excepción existente `SEC-2026-001`, pendiente de resolver antes de su vencimiento. No equivale a una certificación de despliegue.

Siguiente tarea: autorización explícita para que empleados registren entradas/salidas, con perfil adecuado, sin convertir Solo lectura en escritura ni conceder administración. Sigue pendiente el cierre conjunto de inventario e inicio de sesión para VPS.
