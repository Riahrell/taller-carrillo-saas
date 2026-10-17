# Acceso 2.30: prueba de buzón para incorporación MFA

2026-10-17, America/El_Salvador. Base interna; sin correo real, SMTP, HTTP o UI.

## Comportamiento y límites

EnrollmentEmailService toma un pendiente de incorporación y binding del servidor.
Usuario activo, obligación MFA, contraseña/revisión actuales y ausencia de todo
historial de factor son obligatorios. El destinatario se obtiene del correo exacto
registrado, nunca del cliente. Un desafío por pendiente; token aleatorio de 256
bits, solo hash persistido y huella de dirección por usuario/propósito. El borrador
contiene destinatario/token SecretStr tras commit; no se publica ni envía aquí.
No guardar ese borrador en claro: la entrega productiva debe componerse con outbox
cifrada y cuotas dentro de la emisión, no encolar después de otro commit.

Inspección informativa ligada a navegador toma locks acotados sin mutar estado.
No consume desafío, permiso o dirección. No hay GET/POST nuevo; el futuro GET de
un enlace no debe invocar confirmación. Confirmar exige tres secretos distintos:
pendiente de incorporación, binding y token de correo. Consume desafío/fuente,
crea hash de un permiso nuevo y evento global mfa_enrollment_email_confirmed en
un commit. DB/commit/auditoría fallidos no publican permiso o consumo parcial.

Permiso: solo para la futura incorporación del primer factor, nunca sesión,
restablecimiento de contraseña o reemplazo de factor. Conserva quince minutos
desde contraseña, revisión, huella, dirección y navegador. Su consumidor privado
debe componerse con la activación en la misma transacción y hacer rollback si
falla lo posterior. Rechaza historial de factor incluso revocado. No activa una
semilla ni acredita entrega efectiva de un correo real. No añade una marca de
correo verificado a usuarios históricos; esa integración aún está pendiente.

Reiniciar contraseña en el mismo navegador cierra también permisos confirmados;
otro navegador no los cierra por ese hecho. Revocaciones globales cierran todos.
Orden usuario -> pendientes -> desafíos de correo -> sesiones. Reloj después de
locks, estado/dirección releídos. FK compuesta garantiza propietario; checks,
hashes únicos y retención sin DELETE runtime protegen forma/uso. Downgrade
rechaza cualquier desafío o evento nuevo, incluso cerrado/consumido. No protege
frente a escrituras SQL privilegiadas que falsifiquen estado coherente.

## Aislamiento del SaaS

El SaaS pertenece al desarrollador; Carrillo es una empresa afiliada. Carrillo
solo debe ver sus accesos y datos. El selector actual filtra por membresías activas
del usuario y elige automáticamente si tiene una. No existe una consola del dueño
del SaaS o un listado global disponible a clientes. El futuro inventario deberá
validar cada consulta/movimiento contra empresa de sesión del servidor; esta
entrega no implementa inventario ni una consola de plataforma.

## Archivos completos modificados

- `backend/app/modules/access/enrollment_email.py`: servicio/permiso internos.
- `backend/app/modules/access/models.py`, `backend/migrations/versions/0016_enrollment_email.py`: persistencia y evento tipado.
- `backend/app/modules/access/login_challenges.py`, `mfa_pending.py`: cierre global/por navegador.
- `backend/tests/integration/test_enrollment_email.py`: pruebas PostgreSQL.
- `backend/tests/integration/test_mfa_consumption.py`, `test_postgres.py`, `test_user_provisioning.py`: head esperado 0016.
- README, decisiones, aceptación, modelo y contratos/revisión de seguridad.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.30-ci.log 2>&1
```

Primera ejecución: exit 1, 1.071 backend pasados y tres fallos de Alembic check
por nombre distinto de una restricción compuesta entre modelo y migración. Se
alineó el nombre explícitamente y se repitió CI completo, sin retirar checks:

```bash
python3 -m scripts.ci > /tmp/carrillo-access-2.30-ci-final.log 2>&1
```

CI final **exit 0**, proyecto `carrillo-ci-eedd09c22df2`: **1.269 casos**,
1.074 backend (34 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato
(98 archivos), tipos, builds, roundtrip/check de migraciones, HTTP smoke y limpieza
pasaron. Auditorías obligatorias passed/accepted; informe privado:
`.local/security-reports/3be4b3c5d7024694b6193ccd4fd1bc62.json`. Npm conserva
las cuatro entradas SEC-2026-001; no cero hallazgos.

Los 34 casos nuevos prueban hashes/destinatario, lectura sin consumo, confirmación
y permiso de uso único, aislamiento de propósito/navegador, dirección/contraseña/
revisión/actividad actuales, límites exactos, reloj tras locks, revocación global
y por navegador, rechazo de historial de factor, rollback posterior y de commit,
errores reales de permisos/auditoría, FK/check/unicidad/retención, downgrade y dos
conexiones realmente bloqueadas con un solo éxito. No prueban envío/recepción
real, HTTP de correo, activación TOTP o caída física de COMMIT.

Ejecución local **exit 0**: imagen backend construida, backup previo privado
`.local/development/backups/pre-access-2.30-1a2a72c6.dump` (0600, índice
verificado; sin restauración completa), migración aplicada a
`0016_enrollment_email (head)` y Alembic check sin operaciones nuevas. Smoke web
correcto con 10 assets; backend, base de datos, frontend y proxy saludables.

## Ejecución local

Desde la raíz; detenerse si falla el backup. El índice verificado no equivale a
una restauración completa. No se cambian credenciales ni se envían mensajes.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.30-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.30-' + uuid.uuid4().hex[:8] + '.dump')
partial = target.with_suffix('.dump.incomplete')
prefix = base + ['exec', '-T', 'db']
with os.fdopen(os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
    subprocess.run(prefix + ['sh', '-c', 'exec pg_dump --username=carrillo_owner --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl'], stdout=output, check=True, timeout=120)
with partial.open('rb') as source:
    subprocess.run(prefix + ['pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=30)
partial.rename(target)
print('Verified PRE-migration private backup: ' + str(target))
PYBACKUP
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait backend frontend proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

## Riesgos y siguiente tarea

Antes de publicar: outbox cifrada/cuotas/envíos fiables, SMTP/remitente/DNS de correo,
incorporación/recuperación completas, hooks/último admin, reautenticación MFA,
cancelación HTTP, UI, HTTPS, retención y operación de claves. Sin compras, DNS,
servidor remoto o proveedores modificados. SEC-2026-001 conserva cuatro entradas
npm aceptadas hasta 2026-10-31 00:00 UTC; sin ampliar ni declarar cero hallazgos.
Sigue 2.31: entrega cifrada/cuotas y transporte de pruebas, sin correo real todavía.
