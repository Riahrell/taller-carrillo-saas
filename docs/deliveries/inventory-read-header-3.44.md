# 3.44 — Corrección de consultas de permisos

La pantalla de permisos rechazaba la consulta incluso con sesión administrativa porque el cliente no enviaba `X-Expected-Membership`, obligatorio en el servidor. El mismo defecto afectaba a productos y categorías de la consulta operativa. No se modificó ni relajó la autorización del backend.

Archivos: `frontend/src/lib/api/client.ts` admite membresía esperada en GET; `lib/employees/permissions.ts` y `lib/inventory/availability.ts` la envían; `frontend/tests/inventory-availability-api.test.ts` verifica las tres solicitudes con el transporte real y una respuesta simulada que rechaza la ausencia de cabecera.

Validación ejecutada desde `frontend`, con Node 24 en PATH:

```bash
npm test
npm run typecheck
npm run lint
```

Resultado: 337 pruebas aprobadas; tipos y ESLint correctos. Build Docker frontend aprobado. No se repitieron pruebas backend: no cambió su código. No se verificó manualmente una sesión autenticada del usuario.

Actualización local desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Prueba manual: recargar `/permisos` como administrador, seleccionar un miembro de la empresa y comprobar que aparece su permiso vigente. Después conceder consulta a un miembro Solo lectura y verificar productos y filtro por categoría con esa cuenta.

Siguen pendientes los permisos de escritura para empleados y el cierre de despliegue VPS; permanecen los riesgos documentados en 3.43. Registros reales ignorados en `.local/validation/inventory-permissions-header-*`.
