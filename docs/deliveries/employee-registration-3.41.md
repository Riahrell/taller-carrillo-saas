# Entrega 3.41 — Registro por invitación y aprobación

## Resultado

UI `/empleados` para invitaciones y solicitudes, y `/registro` para la cuenta nueva
del empleado. El enlace privado se comparte manualmente, el empleado elige su
contraseña, verifica seis caracteres recibidos por correo y espera aprobación.
No se crea usuario antes de verificar el correo ni membresía antes de aprobar.
Aprobar fija read_only y no concede módulos ni acceso a inventario.

El cliente no elige empresa, rol ni permisos. El código está ligado al intento y
al navegador, tiene hasta diez minutos y cinco fallos máximos. Hay cuotas DB por
IP y correo, reenvío con espera visible, sobre cifrado con propósito independiente,
reclamación de entrega previa a I/O y control de generación ante reenvíos.

Aprobación, membresía y auditoría se confirman en una sola transacción y las
aprobaciones son inmutables para runtime. Repetir no duplica. Las APIs existentes
de inventario permanecen administrativas. Contrato completo y límites:
`docs/employee-registration-contract.md`.

## Archivos cambiados

- `backend/app/main.py`
- `backend/app/modules/access/email_outbox.py`
- `backend/app/modules/access/employee_registration.py`
- `backend/app/modules/access/employee_registration_models.py`
- `backend/app/modules/access/http_security.py`
- `backend/app/modules/access/identity_http.py`
- `backend/app/modules/access/models.py`
- `backend/app/modules/access/registration_routes.py`
- `backend/app/tools/identity_worker.py`
- `backend/migrations/versions/0044_employee_registration.py`
- `backend/tests/integration/test_catalog.py`
- `backend/tests/integration/test_catalog_service.py`
- `backend/tests/integration/test_employee_invitations.py`
- `backend/tests/integration/test_employee_registration.py`
- `backend/tests/integration/test_inventory_schema.py`
- `backend/tests/integration/test_mfa_consumption.py`
- `backend/tests/integration/test_postgres.py`
- `backend/tests/integration/test_purchase_receipts.py`
- `backend/tests/integration/test_purchase_schema.py`
- `backend/tests/integration/test_stock_minimum.py`
- `backend/tests/integration/test_stock_reversals.py`
- `backend/tests/integration/test_user_provisioning.py`
- `backend/tests/test_mail_protection.py`
- `docs/deliveries/employee-registration-3.41.md`
- `docs/employee-invitations-contract.md`
- `docs/employee-registration-contract.md`
- `docs/employee-registration-policy.md`
- `docs/inventory-release-plan.md`
- `docs/permissions.md`
- `frontend/src/app/empleados/page.tsx`
- `frontend/src/app/globals.css`
- `frontend/src/app/registro/page.tsx`
- `frontend/src/components/employee-manager.tsx`
- `frontend/src/components/employee-registration.tsx`
- `frontend/src/components/membership-workspace.tsx`
- `frontend/src/lib/employees/api.ts`
- `frontend/tests/employee-onboarding.test.tsx`

## Validación ejecutada

Desde `/home/riahrell/Documentos/ChatGPT/Saas-inventario-web`:

```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend/app/main.py backend/app/modules/access/employee_registration.py backend/app/modules/access/employee_registration_models.py backend/app/modules/access/registration_routes.py backend/app/modules/access/email_outbox.py backend/app/modules/access/identity_http.py backend/app/modules/access/http_security.py backend/app/modules/access/models.py backend/app/tools/identity_worker.py backend/migrations/versions/0044_employee_registration.py backend/tests/integration/test_employee_registration.py backend/tests/test_mail_protection.py
python3 -m scripts.validate_identity --test-path tests/integration/test_employee_registration.py --test-path tests/integration/test_employee_invitations.py --test-path tests/integration/test_identity_http.py --test-path tests/test_mail_protection.py --test-path tests/test_http_security.py --maxfail 3
python3 -m scripts.security
git diff --check
```

**92 pruebas backend aprobadas en 82,76 s.** Incluyen HTTP real con PostgreSQL
temporal: crear invitación, registrar/verificar con transporte simulado, aprobar,
iniciar sesión por el correo normal y comprobar 403 al inventario. También límites,
resend, binding, revocación, cuenta existente sin attach/reset, doble aprobación,
rollback de auditoría y aislamiento criptográfico de cada propósito. Alembic check
sin cambios pendientes. Ruff y formato correctos.

La primera selección mezclada de integración/unitarios/integración aprobó 86
pruebas y produjo tres errores de fixtures al volver al directorio de integración.
Se agruparon primero todas las pruebas de integración y después los unitarios;
la corrida final anterior terminó con código 0, conservando las comprobaciones.
Durante el desarrollo también se corrigieron nombres duplicados de FK y la
integración inicial del nuevo propósito de correo, detectados por las pruebas.

```bash
cd /home/riahrell/Documentos/ChatGPT/Saas-inventario-web/frontend
CARRILLO_NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$CARRILLO_NODE" node_modules/typescript/bin/tsc --noEmit
"$CARRILLO_NODE" node_modules/eslint/bin/eslint.js .
"$CARRILLO_NODE" node_modules/vitest/vitest.mjs run
"$CARRILLO_NODE" node_modules/prettier/bin/prettier.cjs --check src/lib/employees/api.ts src/components/employee-manager.tsx src/components/employee-registration.tsx src/components/membership-workspace.tsx src/app/empleados/page.tsx src/app/registro/page.tsx src/app/globals.css tests/employee-onboarding.test.tsx
```

**328 pruebas frontend aprobadas**. TypeScript y ESLint correctos. Se comprueban
StrictMode/fragmento eliminado, contraseña fuera del formulario después de enviar,
estado pendiente sin sesión, revisión previa y enlace privado oculto al cambiar de
pestaña. No se afirma haber ejecutado la suite backend completa ni todo el workflow
GitHub en esta entrega. La aceptación visual autenticada queda para el propietario.

Seguridad: detector y secretos de árbol/historial aprobados; Python locks sin
hallazgos. Cuatro avisos npm de desarrollo continúan bajo SEC-2026-001 existente,
sin dependencias ni excepciones nuevas. No equivale a ausencia de vulnerabilidades.

## Entorno local

Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Imágenes compiladas, migración 0044 aplicada, backend/frontend saludables y smoke
HTTP aprobado. La revisión Alembic actual se verificó en el entorno local.

El arranque inicial del worker fue rechazado por la revisión automática debido
al posible envío externo de correo. El propietario autorizó expresamente activar
verificaciones de registro/acceso y recuperación solo cuando se soliciten desde
la aplicación. Después de esa autorización se ejecutó:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps identity-worker
```

Resultado: worker recreado y en ejecución; backend/frontend/proxy/DB saludables.
El worker no tiene healthcheck propio: se confirma estado running, no entrega
real de un mensaje. No se generaron correos de prueba externos adicionales.

No se encolaron correos reales durante las pruebas; se usaron exclusivamente
mensajes y transporte simulados en PostgreSQL temporal. Sin cambios en Contabo.

## Revisión manual una vez habilitado el correo

1. Abrir `/membresias` como administrador y seguir Invitaciones y solicitudes de
   empleados, o abrir `/empleados`. Revisar el correo de una cuenta nueva y crear
   la invitación. Copiar el enlace antes de cambiar de pestaña.
2. Abrirlo en ventana privada sin sesión. Completar correo/contraseña y verificar
   el código recibido. Debe aparecer pendiente de aprobación, sin inventario.
3. Como administrador, actualizar solicitudes, revisar esa incorporación y
   aprobarla. La cuenta debe aparecer read_only en Membresías.
4. Iniciar sesión con la cuenta nueva y su código de acceso. No debe poder abrir
   inventario/catálogo privados. Los permisos concretos se implementan después.
5. Revocar otra invitación sin aprobar y comprobar que ya no se puede registrar
   o aprobar con ella. Cancelar revisiones no cambia nada. Revisar móvil/teclado.

## Pendientes y siguiente tarea

Concesiones y revocación de permisos concretos de inventario con DTO operativos;
no elevar empleados a administradores para abrir el módulo. También vinculación
segura de identidades existentes, retención/limpieza de intentos y recuperación
antes del primer login (la prueba de registro aún no alimenta VerifiedMailbox).
No se declara producción lista. Continúan revisión manual, HTTPS y cierre de
respaldo/recuperación/objetos antes del despliegue.
