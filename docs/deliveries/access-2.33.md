# Acceso 2.33: incorporación TOTP interna y códigos offline

2026-10-17, America/El_Salvador. Avanza incorporación/login del
[plan de cierre de seguridad](../access-security-closure.md). Sin HTTP/QR/UI,
correo real, claves operativas, cambios de cuentas reales, DNS o VPS.

## Comportamiento

Begin intercambia el permiso de buzón una sola vez por setup restringido: token
opaco con hash, semilla cifrada AEAD y caducidad original desde contraseña. Esa
transacción consume permiso y crea setup juntos; no activa factor. La semilla
se entrega una vez después de commit, no se relee ni se regenera al repetir permiso.
Los perfiles TOTP y cifrado/clave externos reutilizan mfa_crypto; UUID de factor
reservado por el servidor, sin ID/usuario/rol/tenant aportado por cliente.

Activate descubre sujeto sin autoridad y toma cuotas MFA IP -> cuenta antes de
usuario -> pendiente -> desafío -> setup. Relee revisión, actividad, contraseña,
correo exacto, prueba consumida/permiso intercambiado, navegador y plazo original.
Cualquier historial de factor impide alta inicial, aun revocado. No es reemplazo.
Código incorrecto confirma fallos de las cuotas compartidas; éxito no las borra.
Reloj después de locks/verificación y al final; expiración revierte activación.

OTP correcto confirma factor (primer contador/hora ya consumidos), reencripta con
clave activa, genera exactamente diez códigos de 128 bits y almacena solo hashes
ligados a usuario/conjunto. Cierra/limpia setup, avanza revisión, revoca sesiones
pendientes de todas las empresas y audita mfa_enrolled en un commit. Los códigos
se retornan una vez después de commit, nunca se recuperan desde DB. Un commit
fallido no publica códigos o semilla ni deja factor parcial. No prueba caída
física de COMMIT o recepción de respuesta por el navegador.

Sin sesión automática: iniciar login normal y esperar un OTP no consumido. Perder
respuesta de códigos tras commit requiere factor vigente y futura regeneración
segura; no reabrir setup. Perder respuesta del setup exige reinicio explícito.
La prueba de buzón no se convierte en User.email_verified por backfill o flag;
la futura recuperación de contraseña debe resolver su elegibilidad explícita.

Reiniciar mismo navegador y revocación global cierran setup/retiran ciphertext;
otro navegador no lo cierra solo por reiniciar. Purga explícita y acotada para
caducados, sin schedule; solo locks de setup sin esperar ancestros. DELETE runtime
no concedido y downgrade con cualquier setup/evento nuevo rechazado. Retención de
backups/WAL y memoria no equivalen a borrado seguro. SQL privilegiado queda fuera
de la garantía de servicios, como en las fases anteriores.

## Archivos completos modificados

- backend/app/modules/access/mfa_enrollment.py: setup, activación y purga internos.
- backend/app/modules/access/models.py y backend/migrations/versions/0019_mfa_enrollment.py.
- backend/app/modules/access/enrollment_email.py: contrato del intercambio de permiso.
- backend/app/modules/access/login_challenges.py: cierre de setups al revocar/reiniciar.
- backend/tests/integration/test_enrollment_email.py: incorporación con identidades sintéticas.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py, test_user_provisioning.py: head 0019.
- README, plan de cierre, contratos/persistencia, aceptación, decisiones, modelo y revisión.

## Validación

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.33-ci.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.33-ci-final.log 2>&1
```

CI inicial **exit 0**, proyecto `carrillo-ci-03752f88b622`: 1.343 casos
(1.148 backend, 7 proxy, 152 frontend, 36 scripts). CI final **exit 0**, proyecto `carrillo-ci-f3f6bff071f4`: **1.346 casos** (1.151 backend, 23 nuevos; 7 proxy,
152 frontend y 36 scripts). Pasaron Ruff/formato (105 archivos), tipos, builds,
roundtrip/check Alembic, HTTP smoke y limpieza. Auditorías passed/accepted;
informe privado `.local/security-reports/218a87f1de274f2d82a05bce26e643d3.json`.
Npm conserva cuatro entradas SEC-2026-001, sin ampliar vencimiento.
Las pruebas nuevas cubren setup sin factor/relectura, hashes offline ligados al
sujeto/conjunto, replay del primer OTP, revisión/revocación global, cuota compartida,
cambio de navegador/identidad/plazo, cierre/purga, fallos de commit y permisos SQL,
claves/AEAD y caducidad posterior al trabajo, concurrencia con bloqueo real y
retención/downgrade. No prueban envíos, uso real de QR ni recepción de códigos. La versión final refuerza bloqueo real
mediante pg_blocking_pids, verifica vínculo de cada hash offline y añade tres
casos de clave/AEAD/caducidad final. No se eliminan checks.
Solo factores/semillas/códigos sintéticos en PostgreSQL efímero; no publicar sus
valores en informes/logs. No se afirma QR, HTTP MFA, SMTP, regeneración offline,
recuperación o custodia/rotación operativa de claves completados.

## Ejecución local

Ejecución local **exit 0**: backend construido, copia previa privada
`.local/development/backups/pre-access-2.33-7b292f8c.dump` (0600, índice verificado;
sin restauración completa), migración aplicada a `0019_mfa_enrollment (head)`.
Alembic check sin operaciones nuevas, smoke web correcto con 10 assets y cuatro
servicios saludables (backend, DB, frontend y proxy). No se crean factores o
claves operativas ni se invoca incorporación sobre usuarios reales.
Logs: `/tmp/carrillo-access-2.33-dev-build.log`, `/tmp/carrillo-access-2.33-migrate.log`,
`/tmp/carrillo-access-2.33-local.log`.

Desde la raíz; detenerse si falla el backup. El índice verificado no equivale a
una restauración completa. No se cambian credenciales ni se envían mensajes.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.33-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.33-' + uuid.uuid4().hex[:8] + '.dump')
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

Sigue recuperación restringida de MFA con código offline, sin sesión de negocio
hasta verificar el reemplazo. Faltan reset de contraseña/correo elegible, hooks
de rol/factor/último admin, reautenticación, HTTP/UI, operación/HTTPS y correo real;
ver criterios de cierre. No activar factores operativos mediante SQL/CLI interna
mientras falten esos recorridos. SEC-2026-001 conserva cuatro entradas npm con
vencimiento 2026-10-31 00:00 UTC, sin ampliar ni declarar cero hallazgos.
