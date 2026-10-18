# Entrega 2.36 — Regeneración interna de códigos offline

Fecha: 2026-10-17. Alcance pequeño del ciclo de identidad; sin HTTP/UI nuevos,
servicio de correo real, claves operativas, cambios de cuentas reales o VPS.

## Resultado y decisiones

El titular con sesión MFA completa reciente puede regenerar sus diez códigos
sin sustituir el factor. El servicio deriva identidad, factor y prueba desde la
sesión persistida. Revalida revisión, huella de contraseña, factor, membresía y
sesión activas; ambos factores deben estar dentro de cinco minutos desde el más
antiguo, comprobado tras locks y al terminar. Conforme al contrato, un login MFA
reciente puede acreditar ambos: no se exige introducir otro OTP inmediatamente.
Sesión antigua o confirmación solo de contraseña no sirven. La reautenticación
MFA de sesiones antiguas sigue pendiente; mientras tanto necesitan nuevo login.

Invalida conjunto anterior, crea diez hashes, avanza revisión, cierra todas las
sesiones/pendientes y audita mfa_codes_regenerated en una transacción. Mantiene
semilla y contador; no consume otro TOTP. No exige admin ni permite actuar sobre
otro usuario. No evalúa secretos nuevos ni crea una cuota por propósito; las
pruebas combinadas de login ya pasaron cuotas de contraseña/MFA compartidas.

Los códigos salen una sola vez después del commit. Se revoca la sesión que
permitió regenerar; repetición/concurrencia no revela otro conjunto. Si se pierde
la respuesta, volver a login completo y regenerar, sin relectura. La evidencia de
regeneración bloquea downgrade; errores de escritura/commit/caducidad revierten
conjunto, revisión, auditoría y revocación. No promete entrega de respuesta ante
caída de red o borrado de memoria/backups/WAL.

## Archivos completos modificados

- backend/app/modules/access/mfa_regeneration.py: caso de uso transaccional.
- backend/app/modules/access/models.py: evento tipado.
- backend/migrations/versions/0022_mfa_code_regeneration.py: checks y downgrade.
- backend/tests/integration/test_mfa_recovery.py: 17 casos de regeneración.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py y test_user_provisioning.py: head 0022.
- README y docs/acceptance.md, access-security-closure.md, data-model.md,
  decisions.md, mfa-persistence-contract.md, mfa-recovery-contract.md,
  security-review.md y esta entrega.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.36-ci.log 2>&1
```

CI **exit 0**, proyecto `carrillo-ci-5733d305525a`: **1.421 casos** = 1.226
backend (17 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato (113 archivos),
tipos, builds, migraciones y HTTP smoke pasaron; limpieza del proyecto aislado
completada. Informe saneado privado de seguridad:
`.local/security-reports/eda158781dbb473387469d97664e43cb.json`.
Auditorías passed/accepted; npm conserva cuatro entradas de SEC-2026-001.

Casos: éxito/diez hashes/semilla y contador intactos, repetición, nueve rechazos
de autoridad obsoleta o ausente, límite exacto y justo antes, rollback por commit,
INSERT real denegado de códigos/auditoría y caducidad tras generación; solicitudes
concurrentes con un solo resultado y downgrade rechazado con evidencia. PostgreSQL
efímero e identidades sintéticas; no prueba teléfonos ni caída física de COMMIT.

## Ejecución local

Ejecución **exit 0**: imagen backend construida; copia privada previa
`.local/development/backups/pre-access-2.36-0877c087.dump` (0600, 68.447 bytes,
índice verificado, sin restauración completa); migración
`0022_mfa_code_regeneration (head)` aplicada. Alembic check sin operaciones nuevas,
smoke local correcto y backend/DB/frontend/proxy saludables. Logs:
`/tmp/carrillo-access-2.36-dev-build.log`, `/tmp/carrillo-access-2.36-backup.log`,
`/tmp/carrillo-access-2.36-migrate.log`, `/tmp/carrillo-access-2.36-local.log`.
No se regeneran códigos sobre usuarios operativos.

Comandos exactos; detenerse si falla copia/validación previa. El índice de copia
verificado no equivale a una restauración completa.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.36-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.36-' + uuid.uuid4().hex[:8] + '.dump')
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

MFA aún no está conectado a HTTP/UI. Pendientes reautenticación, avisos, hooks de
roles/último admin, reset de contraseña y operación/HTTPS/claves/restauración.
Siguiente tarea: elegibilidad explícita de correo para reset de contraseña sin
eliminar MFA ni códigos sin usar; respetar separación de propósitos de correo.
SEC-2026-001 mantiene cuatro entradas npm y vence 2026-10-31 00:00 UTC; sin
ampliación ni afirmación de cero vulnerabilidades. Criterios de cierre en
access-security-closure.md; no habilitar factores reales antes de su integración.
