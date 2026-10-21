# Entrega 3.40 — Invitaciones privadas de empleados

## Resultado y alcance

Implementados servicio, persistencia, migración y API administrativa para crear,
listar y revocar invitaciones ligadas al correo y empresa autenticada. Código
aleatorio de 256 bits mostrado una vez, almacenado como SHA-256, vigencia 24 horas,
índice único por invitación abierta y correo/empresa, y límite 100 sin revocar.
Auditoría tipada CREATED/REVOKED transaccional e inmutable para runtime.

No crea cuentas ni concede acceso. La pantalla, consumo público/verificación de
correo, aprobación y permisos de inventario se integrarán después. No hay correos
reales, cambios de stock/financieros, cambios frontend ni despliegue en Contabo.
Contrato completo y errores: `docs/employee-invitations-contract.md`.

## Comandos exactos

Desde `/home/riahrell/Documentos/ChatGPT/Saas-inventario-web`:

```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend/app/main.py backend/app/modules/access/models.py backend/app/modules/access/employee_invitation_models.py backend/app/modules/access/employee_invitations.py backend/app/modules/access/invitation_routes.py backend/migrations/versions/0043_employee_invitations.py backend/tests/integration/test_employee_invitations.py
python3 -m scripts.validate_identity --test-path tests/integration/test_employee_invitations.py --test-path tests/integration/test_user_provisioning.py --test-path tests/integration/test_stock_reversals.py --test-path tests/integration/test_postgres.py --maxfail 3
python3 -m scripts.validate_identity --test-path tests/integration/test_employee_invitations.py --maxfail 3
python3 -m scripts.security
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

## Resultados reales

- Primera corrida de regresión: **62 pruebas aprobadas en 106,35 s**, incluyendo
  alta administrativa, PostgreSQL, correcciones de inventario y las pruebas de
  invitaciones disponibles entonces.
- Corrida final de invitaciones: **12 pruebas aprobadas en 49,63 s**, incluyendo
  las restricciones finales de columnas/DELETE/TRUNCATE, rollback de revocación,
  confirmación de identidad y bajada protegida. Las corridas se superponen;
  no se suman como pruebas distintas.
- Alembic check: sin operaciones pendientes. Ruff y formato: correctos.
- Seguridad: detector y secretos de árbol/historial aprobados sin hallazgos;
  Python locks sin hallazgos. Cuatro avisos npm de desarrollo permanecen bajo
  la excepción existente SEC-2026-001, sin nuevas excepciones ni dependencias.
- Imagen backend compilada, migración local 0043 comprobada y backend saludable.
  Smoke HTTP aprobado: HTML español, diez assets, API de mismo origen, 404,
  cabeceras y rechazo de escrituras sin origen/CSRF válidos.

Las pruebas nuevas ejercitan secreto/hash sin identidades nuevas, correo
normalizado, caducidad, duplicados y concurrencia, paginación aislada por empresa,
revocación repetida, rol/contexto/sesión vigentes, HTTP/CSRF/entradas estrictas,
rollback de evidencia tanto al crear como al revocar y permisos reales de DB.
Incluyen bajada protegida con evidencia. Alembic compara esquema y metadata.
Las comprobaciones de head existentes se actualizan a 0043; los destinos de
pruebas de downgrade conservan las versiones funcionales que validaban.

No se repite la batería frontend, que no cambió, ni se afirma haber ejecutado
la batería backend completa o el workflow completo de GitHub en esta entrega.

## Riesgos y siguiente tarea

El registro autónomo aún no está disponible. No presentar este servicio como
incorporación completa. La siguiente tarea es consumir la invitación y verificar
correo sin conceder membresía activa, con estados pendientes y posterior
aprobación del administrador. Cuentas existentes requieren prueba de identidad;
conocer su correo no permite adjuntarlas ni cambiarles contraseña.

Después se conectarán los permisos explícitos de empleados a las rutas operativas,
sin reutilizar DTO administrativos de costos ni elevar empleados a administradores.
La API actual de inventario mantiene su permiso administrativo previo.
La preparación del despliegue sigue pendiente de ese bloque y de la revisión
manual y operativa ya documentada.

## Archivos cambiados

- `backend/app/main.py`
- `backend/app/modules/access/employee_invitation_models.py`
- `backend/app/modules/access/employee_invitations.py`
- `backend/app/modules/access/invitation_routes.py`
- `backend/app/modules/access/models.py`
- `backend/migrations/versions/0043_employee_invitations.py`
- `backend/tests/integration/test_catalog.py`
- `backend/tests/integration/test_catalog_service.py`
- `backend/tests/integration/test_employee_invitations.py`
- `backend/tests/integration/test_inventory_schema.py`
- `backend/tests/integration/test_mfa_consumption.py`
- `backend/tests/integration/test_postgres.py`
- `backend/tests/integration/test_purchase_receipts.py`
- `backend/tests/integration/test_purchase_schema.py`
- `backend/tests/integration/test_stock_minimum.py`
- `backend/tests/integration/test_stock_reversals.py`
- `backend/tests/integration/test_user_provisioning.py`
- `docs/deliveries/employee-invitations-3.40.md`
- `docs/employee-invitations-contract.md`
- `docs/employee-registration-policy.md`
- `docs/inventory-release-plan.md`
- `docs/permissions.md`
