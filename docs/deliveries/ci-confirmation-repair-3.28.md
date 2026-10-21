# Reparación de CI y contrato de confirmación 3.28

## Cambios

- La confirmación de identidad conserva la consulta pendiente cuando el padre
  cambia sus callbacks; evita reinicios y respuestas tardías tras desmontar.
- Las pruebas esperan la consulta asíncrona de seguridad y distinguen sesiones
  de correo confirmadas de la confirmación histórica por contraseña/TOTP.
- Las pruebas históricas de TOTP producen sus desafíos mediante el autenticador
  interno real; la entrada pública se comprueba mediante código por correo.
- Las pruebas de acceso y Nginx recorren contraseña, solicitud, worker con
  transporte capturado y confirmación. No envían correos externos.
- Cerrar sesión revoca la sesión y cancela los desafíos de correo vinculados en
  una transacción. Se verifica también el rollback ante fallo de commit.
- Las cookies de sesión se verifican como no persistentes, manteniendo las
  comprobaciones de alcance, HttpOnly, Secure, SameSite y caducidad del servidor.
- Se corrige el nombre duplicado de una restricción de PostgreSQL en 0037.
  La migración 0038 normaliza bases que ya aplicaron esa versión; no cambia
  la expresión de seguridad ni borra datos. Se prueba también ese caso anterior.
- Se documenta el registro de empleados con aprobación y autorización de la
  empresa en `docs/employee-registration-policy.md`. Aún no está implementado.

## Archivos

Fuentes: `frontend/src/components/identity-confirmation.tsx`,
`backend/app/modules/access/http_service.py`,
`backend/app/modules/access/routes.py`, migraciones 0037 y 0038.

Pruebas frontend: identity-confirmation, membership-creation,
membership-workspace e inventory-workspace. Pruebas backend: fixture de identidad,
acceso HTTP, identidad HTTP, Nginx, cookies, reautenticación y consumidores
históricos de la fixture. Las aserciones de versión de esquema apuntan a 0038.

## Validación reproducible

Desde la raíz del repositorio, con Docker disponible:

```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
python3 -m scripts.ci
git diff --check
```

La CI genera credenciales temporales y PostgreSQL desechable `carrillo_test` en
proyectos `carrillo-ci-*`; no usa ni modifica los registros de desarrollo.

## Alcance y siguiente entrega

El registro autónomo y los permisos específicos de empleados quedan pendientes;
no se ha publicado un registro público ni concedido permisos nuevos. La política
acordada deniega acceso por registrarse y exige aprobación de empresa en servidor.
Las entradas/salidas manuales y los vínculos con ventas/órdenes continúan en
`docs/inventory-release-plan.md`. El despliegue a Contabo sigue pendiente.

## Relación completa de archivos de esta entrega

```text
backend/app/modules/access/http_service.py
backend/app/modules/access/routes.py
backend/migrations/versions/0037_email_browser_login.py
backend/migrations/versions/0038_mailbox_constraint_name.py
backend/tests/integration/identity_http_fixture.py
backend/tests/integration/proxy_access.py
backend/tests/integration/test_access_routes.py
backend/tests/integration/test_catalog.py
backend/tests/integration/test_catalog_http.py
backend/tests/integration/test_catalog_service.py
backend/tests/integration/test_identity_http.py
backend/tests/integration/test_inventory_http.py
backend/tests/integration/test_inventory_schema.py
backend/tests/integration/test_membership_routes.py
backend/tests/integration/test_mfa_consumption.py
backend/tests/integration/test_postgres.py
backend/tests/integration/test_product_image_http.py
backend/tests/integration/test_purchase_receipts.py
backend/tests/integration/test_purchase_schema.py
backend/tests/integration/test_reauthentication_routes.py
backend/tests/integration/test_recent_administration.py
backend/tests/integration/test_user_creation_routes.py
backend/tests/integration/test_user_provisioning.py
backend/tests/test_http_security.py
docs/deliveries/ci-confirmation-repair-3.28.md
docs/employee-registration-policy.md
frontend/src/components/identity-confirmation.tsx
frontend/tests/identity-confirmation.test.tsx
frontend/tests/inventory-workspace.test.tsx
frontend/tests/membership-creation.test.tsx
frontend/tests/membership-workspace.test.tsx
```

## Resultados ejecutados

CI completa con salida 0, proyecto `carrillo-ci-b4f36ae23ad6`:

- 46 pruebas de scripts correctas.
- 258 pruebas frontend correctas en 18 archivos.
- 1.870 pruebas backend correctas, 296,54 segundos.
- 11 pruebas de proxy real correctas, 13,29 segundos.
- Workflow, Ruff, formato, TypeScript, ESLint y builds correctos.
- Smoke HTTP correcto; sin escritura operacional ni SMTP real.
- Python locks y detección de secretos sin hallazgos.
- npm mantiene cuatro hallazgos aceptados por la excepción de desarrollo ya
  existente; no se añadieron excepciones ni se desactivaron controles.
- Las cuatro imágenes de runtime: cero hallazgos HIGH/CRITICAL y cero hallazgos
  totales reportados. Se generó evidencia SBOM privada.

Registro local: `.local/validation/confirmation-ci-verified.log`.

La aplicación local se reconstruyó y actualizó. La base quedó en
`0038_mailbox_constraint_name (head)`; backend y frontend saludables.
Smoke sobre `http://localhost:8080` correcto después del reinicio.
Comandos ejecutados con la configuración privada local existente:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate python -m alembic current
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Para revisión manual: iniciar sesión por correo y abrir productos, inventario y
membresías sin otra confirmación de contraseña; cerrar sesión y comprobar que
el acceso anterior deja de funcionar. El navegador reconocido conserva solo su
verificación de quince días: la contraseña sigue siendo obligatoria al iniciar
otra sesión. La restauración de sesiones al abrir un navegador depende de sus
ajustes; Cerrar sesión invalida siempre el acceso en el servidor.
