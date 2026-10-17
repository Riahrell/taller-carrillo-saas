# Entrega: servicio interno de confirmación reciente (acceso 2.16)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-recent-authentication-service`.

## Resultado y alcance

Servicio interno de verificación de contraseña actual para una sesión admin,
con contexto obligatorio, límites persistentes compartidos con login y rotación
de token atómica. Conserva vida absoluta de ocho horas; no revive sesiones
expiradas/revocadas. Guardia transaccional de confirmación de cinco minutos,
ligada a sesión/membresía y huella del hash actual. No comparte confirmación
entre empresas o sesiones, ni la renueva por actividad ordinaria.

Migración 0008 agrega fecha/huella opcionales sin defaults ni backfill; sesiones
existentes permanecen sin confirmación. Downgrade revoca sesiones confirmadas
antes de retirar columnas, conservando sesiones ordinarias y auditoría.

**Todavía no hay endpoint/formulario de reautenticación ni llamadas a la guardia
desde alta/cambio administrativos. La brecha operativa sigue abierta.** Esta
entrega prepara el servicio y contrato, manteniendo ejecutable la aplicación.
No representa MFA, resistencia a phishing o preparación para producción.

## Archivos completos creados o modificados

- `backend/app/modules/access/reauthentication.py`: modelo estricto de contraseña
  oculta/no serializable y guardia de confirmación dentro de la transacción.
- `backend/app/modules/access/authentication.py`: servicio de confirmación,
  locks, savepoint, rotación, límites compartidos y saneamiento de fallos;
  extracción común del registro de fallos, conservando política de login.
- `backend/app/modules/access/models.py`: fecha/huella y CHECK de coherencia.
- `backend/migrations/versions/0008_session_reauthentication.py`: ampliación
  sin backfill y downgrade con revocación de sesiones confirmadas.
- `backend/tests/test_reauthentication_inputs.py`: 12 casos de contrato, Unicode,
  límites, secreto excluido y campos de autoridad prohibidos.
- `backend/tests/integration/test_reauthentication.py`: 42 casos con PostgreSQL,
  rotación, límites, expiración, permisos, rollback, aislamiento y concurrencia.
- `backend/tests/integration/test_postgres.py` y `test_user_provisioning.py`:
  revisión head actualizada, preservando prueba de rechazo de pérdida de auditoría.
- `docs/reauthentication-contract.md`: alcance, autoridad, ventanas, locks,
  rotación, transacciones, migración y aceptación pendiente de HTTP/UI.
- `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/data-model.md`, `docs/decisions.md`, `docs/permissions.md`,
  `docs/security-review.md`, `docs/staging-plan.md` y este registro: estado,
  brechas, dominio informado y siguiente tarea.

Sin cambios frontend, dependencias nuevas, secretos o cuentas operativas creadas.
Los archivos se entregan completos en el repositorio.

## Comandos exactos ejecutados

Desde la raíz, comprobación local y pipeline completo aislado:

```bash
.venv/bin/python -m pytest backend/tests/test_reauthentication_inputs.py -q
.venv/bin/python -m ruff check backend scripts
.venv/bin/python -m ruff format --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.16-final-ci.log 2>&1
```

La CI genera proyecto/credenciales propios, PostgreSQL temporal en tmpfs y
guards que rechazan bases operativas. Downgrades y casos destructivos solo ahí.

Copia privada previa a migrar desarrollo:

```bash
python3 - <<'PY'
import os
from pathlib import Path
import subprocess
import uuid
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.16-' + uuid.uuid4().hex[:8] + '.dump')
prefix = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml', 'exec', '-T', 'db']
with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
    subprocess.run(prefix + ['sh', '-c', 'exec pg_dump --username=carrillo_owner --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl'], stdout=output, check=True, timeout=120)
with target.open('rb') as source:
    subprocess.run(prefix + ['pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=30)
print('Private local backup created; archive listing verified: ' + str(target))
PY
```

Copia creada: `.local/development/backups/pre-access-2.16-a6892cf3.dump`, archivo
0600 fuera de Git/build. Índice aprobado; **no se ensayó restauración completa**.
Contiene datos privados/hashes: no publicarla ni adjuntarla a conversaciones.

Actualización y ejecución local con entorno existente, sin regenerar credenciales:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.16-dev-build.log 2>&1
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

## Resultados reales

- Contrato local: **12 passed en 0.13s**.
- Primer pipeline `carrillo-ci-f7029e682b8c`: **639 passed, 3 failed**, todos los
  fallos en comprobación de espera de locks. La consulta a pg_stat_activity no
  exponía el estado de otros roles al owner sin privilegios de monitoreo. Se
  cambió la prueba a pg_locks, sin ampliar privilegios de owner/app ni relajar
  el servicio. Se añadieron cuatro casos adicionales de seguridad.
- Segundo `carrillo-ci-ad6c128fc7c4`: detenido por formato Ruff de esa consulta;
  corregido antes de la ejecución final. Ninguno se cuenta como aprobado.
- Final `carrillo-ci-70654b84ab20`, exit 0, **CI passed**: **646 backend en
  45.89s** (226 unitarios y 420 PostgreSQL), **6 por proxy en 4.61s**,
  **129 frontend en 1.24s** y **13 scripts/configuración**. **794 pruebas**,
  54 nuevas frente a 2.15. Casos por proxy contados una sola vez.
- Ruff/formato en 69 archivos Python, pip check, actionlint, ESLint, Prettier,
  tipos, imágenes, migraciones y smoke aprobados. Frontend sin cambios reutilizó
  build en caché; no se afirma compilación nueva de Next.js en esta entrega.
- Token anterior rechazado después de commit; nuevo solo en SecretStr de
  respuesta interna, hash almacenado, IDs/expiración originales conservados.
  Rechazo de contraseña/contexto conserva sesión y confirma contadores;
  fallo inyectado de hashing/commit revierte prueba, token y contadores.
- Límites de login/confirmación compartidos, bloqueo sin hashing adicional y
  reapertura exacta a 15 minutos. Hash almacenado inválido toma dummy y rechaza.
  Cinco minutos exactos, reloj hacia atrás y caducidad durante hashing rechazan.
- Dos trabajadores con el mismo token: solo uno rota y verifica contraseña;
  el otro espera realmente un lock y rechaza. Revocación de usuario y confirmación
  se probaron en ambos órdenes sin resucitar sesión. Sin cruce de empresas/sesiones.
- Guardia revalida rol, actor, revocación y hash actual; rechazo revierte una
  transacción sensible simulada. CHECK rechaza estado parcial, huella inválida
  y fecha fuera de vida. Roundtrip revoca token confirmado, conserva sesión
  ordinaria y no rellena pruebas. Pruebas anteriores de auditoría y login repetidas.
- Todos los proyectos temporales del pipeline se limpiaron. No se ejecutó nuevo
  recorrido manual de navegador: esta entrega no cambia pantallas ni agrega HTTP.
- Local: backup previo, migración **0008_session_reauthentication (head)**,
  Alembic check **No new upgrade operations detected**, cuatro servicios saludables
  y smoke aprobado. Primer smoke dentro del sandbox fue bloqueado al abrir socket;
  repetido fuera del sandbox con autorización, exit 0. HTML español, diez assets,
  API mismo origen, 404 y rechazo sin CSRF/origen verificados en localhost:8080.

## Riesgos y siguiente tarea

La guardia interna todavía no protege las rutas actuales. **2.17**: contrato y
adaptador HTTP con sesión/contexto, CSRF/origen, slots de hashing compartidos,
cookies/CSRF rotados y errores sin secretos, probados por HTTP/proxy. Después UI
y activación de guardia dentro de las transacciones de alta/cambio, con recorrido
completo. Una comprobación frontend o preflight separado no sustituye esa guardia.

Rotación invalida inmediatamente token viejo; respuesta perdida requiere login.
Logout con cookie antigua no identifica el token nuevo: coordinar escrituras y
rotación entre pestañas al integrar UI. La prueba de cinco minutos no es un
permiso de un solo uso ni está vinculada al contenido de una operación.
MFA/passkeys, recuperación/cambio de contraseña, auditoría de autenticación,
limpieza de throttles, dependencias/secretos, navegador y restore siguen pendientes.
No se requiere comprar servicios para esta entrega. No hay evaluación ASVS completa.

El usuario informa que adquirió **taller-carrillo.com** y apuntó DNS a su VPS
Contabo, compartido con imágenes. DNS/proxy/TLS todavía sin verificar desde
aquí; sin SSH, cambios DNS o publicación. El dominio se documenta como contexto,
sin IP pública ni origen hardcodeado. Ver `../staging-plan.md` para revisión
del servidor y staging aislado antes de desplegar. No hubo push remoto.
