# Entrega 2.38 — Emisión interna cifrada del reset de contraseña

Fecha: 2026-10-17. Se separa emisión de consumo para revisar propósito, límites y
rollback antes de habilitar cambios de contraseña. No hay endpoint/formulario,
consumidor de reset, proveedor real o envíos. No cambia cuentas operativas o VPS.

## Resultado

PasswordResetIssuer.issue acepta correo normalizado como login y peer IP validado.
Todas las salidas normales son None: elegible, desconocido, no verificado,
inactivo o limitado. No retorna token, destinatario o resultado de elegibilidad.
Esto no demuestra igualdad de tiempos; la protección HTTP/temporal contra
enumeración sigue pendiente antes de exponer solicitudes públicamente.

Cuotas propias persistentes: IP veinte y dirección tres por quince minutos,
sesenta segundos entre solicitudes aceptadas por dirección. Cuentan también
solicitudes desconocidas/no elegibles. IP contabiliza denegaciones de dirección;
se bloquea antes de crear cuotas de otra dirección cuando IP está saturada.
Ventana no se extiende por rechazos; IPv4 mapeada comparte identidad IP. HMAC por
propósito/ámbito guarda claves sin dirección en claro. No altera cuotas de login,
MFA o incorporación. Orden de locks IP → dirección → usuario → evidencia de buzón.

Si el buzón es elegible bajo lock, persiste token aleatorio de 256 bits mediante
hash con dominio password-reset-token-v1:, propietario/verificación con FK
compuesta, revisión, huella de contraseña y plazo absoluto de quince minutos.
La misma fila guarda staging destinatario/token cifrado AES-GCM con AAD de reset
ligada a usuario/id/plazo/key id. No guarda una copia en claro o devuelve token.
MailProtection requiere propósito PASSWORD_RESET para este emisor; conserva
compatibilidad del dominio previo de incorporación. Claves inyectadas sin defaults.

Solicitar otro enlace no cierra el anterior ni renueva su plazo. No toca contraseña,
revisión, sesiones, autenticaciones pendientes, factor o códigos offline. Evita
revocar acceso por solicitudes no autenticadas; cierre global pertenece al futuro
consumo confirmado. Tener un payload no autoriza envío o reset: ambos deberán
revalidar dirección/evidencia, revisión, contraseña y caducidad actuales.

Purga explícita limitada cierra caducados y retira ciphertext sin reembolsar cuota
o borrar metadatos. Runtime sin DELETE de desafíos; downgrade con datos/cuotas
rechazado. No hay programación, política final de retención de cuotas, borrado
seguro de memoria/backups/WAL ni garantía de entrega. Un fallo de cifrado/SQL/
commit/plazo revierte desafío, staging y cuota juntos.

## Archivos completos modificados

- backend/app/modules/access/password_reset.py: contrato interno, cuotas, emisión y purga.
- backend/app/modules/access/email_outbox.py: propósito criptográfico configurado y HMAC de solicitud de reset.
- backend/app/modules/access/models.py y backend/migrations/versions/0024_password_reset_requests.py: desafíos/cuotas/FK/constraints/downgrade.
- backend/tests/integration/test_enrollment_email.py: 18 casos nuevos y limpieza efímera.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py y test_user_provisioning.py: head 0024.
- README, docs/email-recovery-design.md, access-security-closure.md, decisions.md,
  data-model.md, acceptance.md, security-review.md, mfa-persistence-contract.md y esta entrega.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.38-ci.log 2>&1
```

CI **exit 0**, proyecto `carrillo-ci-f1a23d55b55a`: **1.458 casos** = 1.263
backend (18 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato (117 archivos),
tipos, builds, migraciones y smoke HTTP pasaron; proyecto aislado limpiado.
Informe privado saneado de seguridad:
`.local/security-reports/10c7d0418e124eb68fcc045f50fba5cc.json`.
Escaneos passed/accepted; npm conserva cuatro entradas SEC-2026-001.

Casos nuevos: emisión cifrada y hash/propósito/plazo/propietario, rechazo de token
como permiso de incorporación, cuatro estados no elegibles con igual retorno,
cuotas/cooldown/ventana exacta e IPv4 mapeada, abuso de direcciones desconocidas,
AAD intercambiada y propósito distinto con mismas claves, rollback por commit/
INSERT denegado/cifrado/plazo/reloj, solicitudes concurrentes con un staging,
purga exacta con retención, FK/constraints/DELETE/downgrade. Sesión MFA real,
contraseña/revisión/factor/contador/códigos intactos tras solicitar; carrera con
cambio de correo sin staging para dirección antigua. Identidades y claves
sintéticas en PostgreSQL efímero; no prueba SMTP, HTTP de reset, cambio de
contraseña, tiempo constante o caída física de COMMIT.

## Ejecución local

Ejecución **exit 0**: backend construido; copia privada previa
`.local/development/backups/pre-access-2.38-c1457239.dump` (0600, 72.876 bytes,
índice verificado; sin restauración completa). Migración
`0024_password_reset_requests (head)` aplicada; Alembic check sin operaciones nuevas.
Smoke local correcto con 10 assets y backend/DB/frontend/proxy saludables.
Logs: `/tmp/carrillo-access-2.38-dev-build.log`, `/tmp/carrillo-access-2.38-backup.log`,
`/tmp/carrillo-access-2.38-migrate.log`, `/tmp/carrillo-access-2.38-local.log`.
No se solicitan resets ni se cambian contraseñas de usuarios operativos.

Comandos exactos; detenerse si falla copia o CI. Índice de copia verificado no
equivale a restauración completa. No ejecutar solicitudes sobre usuarios reales.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.38-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.38-' + uuid.uuid4().hex[:8] + '.dump')
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

Sigue consumo del token con contraseña nueva: validación completa, uso único,
cierre de otros enlaces/sesiones/pendientes, revisión/auditoría y aviso persistido
en la misma transacción, conservando factor y códigos MFA. Después consumidor de
correo con revalidación/deadlines, verificación general para cuentas sin MFA,
reautenticación, hooks de roles/último admin, HTTP/UI y operación. No declarar
recuperación por correo disponible ni activar factores reales antes de cerrar
los recorridos. SEC-2026-001 mantiene cuatro entradas npm hasta 2026-10-31 00:00 UTC,
sin extenderse ni afirmar cero vulnerabilidades. Ver access-security-closure.md.
