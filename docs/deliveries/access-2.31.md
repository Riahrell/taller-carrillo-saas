# Acceso 2.31: staging cifrado y cuotas de correo MFA

2026-10-17, America/El_Salvador. Base interna, sin SMTP, worker, HTTP nuevo o UI.

## Resultado y alcance

La emisión integra desafío de buzón, reserva de cuota y mensaje cifrado en un
commit. La falta de claves/cifrado, permisos SQL o commit revierte los tres.
Un desafío por pendiente; destinatario registrado del usuario y caducidad original
sin extensión. El borrador SecretStr sigue siendo interno y posterior al commit:
no es una interfaz de envío ni debe guardarse en claro. No hay correos reales.

Tres mensajes/cuenta y veinte/IP por ventana de quince minutos, y sesenta segundos
entre reservas de una cuenta. Cuotas PostgreSQL con locks ordenados, HMAC con clave
externa; IPv4 mapeada en IPv6 comparte identidad. No las reinicia contraseña correcta,
reinicio del navegador o confirmación. Duplicados no gastan cuota. Solo solicitudes
con pendiente/contraseña válidos alcanzan esta reserva; HTTP/abuso previo no integrados.
Los locks se obtienen antes de releer reloj; expiración y regresión rechazan emisión.
Ventana desde su creación persistida, no límite deslizante; una solicitud
rechazada puede crear filas con cantidad cero, sin gastar cuota. Retención/limpieza de
cuotas y dimensionamiento de tráfico aún pendientes.

AES-256-GCM, nonce aleatorio de doce bytes, AAD por versión/propósito, usuario,
desafío, caducidad y clave-ID. Destinatario y token están dentro del ciphertext.
La semántica de integridad/AAD sigue la [documentación de cryptography](https://cryptography.io/en/latest/hazmat/primitives/aead/).
Keyring inyectado, sin claves por defecto ni producción configurada; clave HMAC
independiente. Rotar cifrado conserva lectura de claves antiguas y las mismas cuotas.
Descifrar no autoriza enviar: el futuro consumidor debe revalidar usuario, revisión,
contraseña, dirección, navegador/propósito, consumo, revocación y caducidad.

Confirmar/revocar descarta el payload en la misma transacción; purga explícita acotada
para caducados, SKIP LOCKED y sin bloquear ancestros. No está programada. Se retienen
metadatos, no ciphertext, tras descarte; backups/WAL requieren retención propia.
No se hace backfill ni se encolan desafíos viejos. Downgrade con filas de cola o cuota
rechazado, incluso descartadas/cero. SQL privilegiado sigue fuera de esa garantía.

Se dividió el objetivo anunciado para mantener revisión pequeña: 2.31 es persistencia,
cifrado y cuotas. **2.32 será consumidor/transporte simulado**, leases/reintentos,
revalidación y resultados ambiguos. No se presenta el cifrado como entrega fiable.
No cambia el aislamiento: Carrillo ve sus membresías/datos; sin consola global nueva.
Orden normal: usuario -> pendiente -> cuotas ordenadas (al emitir) -> desafío ->
outbox; mantenimiento solo bloquea outbox y no espera ancestros.

## Archivos completos modificados

- backend/app/modules/access/email_outbox.py: protección, cuota y descarte/purga.
- backend/app/modules/access/enrollment_email.py: composición transaccional.
- backend/app/modules/access/login_challenges.py: descarte al revocar/reiniciar.
- backend/app/modules/access/models.py y backend/migrations/versions/0017_enrollment_mail_outbox.py.
- backend/tests/test_mail_protection.py y backend/tests/integration/test_enrollment_email.py.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py y test_user_provisioning.py: head 0017.
- README, aceptación, decisiones, modelo, contratos y revisión de seguridad.

## Validación

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_mail_protection.py -q
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.31-ci.log 2>&1
```

13 pruebas de cifrado locales pasaron. Primera ejecución completa: exit 0,
proyecto carrillo-ci-dadf2a99f444, 1.297 casos (1.102 backend, 7 proxy, 152 frontend,
36 scripts). En revisión posterior se ordenaron también los locks de tablas del
downgrade empezando por usuarios/pendientes y se añadieron dos casos del reloj
tras cuotas. No hubo fallos ocultados ni checks retirados. Versión final:

```bash
python3 -m scripts.ci > /tmp/carrillo-access-2.31-ci-final.log 2>&1
```

CI final **exit 0**, proyecto `carrillo-ci-29f93cb54054`: **1.299 casos**,
1.104 backend (30 nuevos: 13 cifrado y 17 PostgreSQL), 7 proxy, 152 frontend y
36 scripts. Pasaron Ruff/formato (101 archivos), tipos, builds, roundtrip/check
Alembic, HTTP smoke y limpieza. Auditorías passed/accepted; informe privado
`.local/security-reports/9d4e11756c4c4a82a29b946cf59d65f0.json`.
Npm conserva cuatro entradas de SEC-2026-001, sin ampliar su vencimiento.
Las pruebas nuevas cubren rotación/tamper/AAD, persistencia cifrada, cuotas/cooldown,
reloj tras locks, rechazo sin claves, descarte/purga exacta, fallos reales de SQL,
fallo sintético de cifrado/commit y dos emisores realmente bloqueados con una sola
reserva. No se declaran cero hallazgos.
Pruebas usan claves/usuarios sintéticos, sin contactar buzones o proveedor.
No se afirma funcionamiento de SMTP, worker, entrega física ni caída física de COMMIT.

## Ejecución local

Ejecución local **exit 0**: imagen final backend construida, copia previa
`.local/development/backups/pre-access-2.31-af2a264e.dump` privada 0600 con índice
verificado (sin restauración completa), migración aplicada a
`0017_enrollment_mail_outbox (head)` y Alembic check sin operaciones nuevas.
Smoke web correcto con 10 assets; backend, DB, frontend y proxy saludables.
Logs: `/tmp/carrillo-access-2.31-dev-build-final.log`,
`/tmp/carrillo-access-2.31-migrate.log` y `/tmp/carrillo-access-2.31-local.log`.
No se han generado/configurado claves operativas de correo ni invocado emisión
para cuentas reales.

Desde la raíz; detenerse si falla el backup. El índice verificado no equivale a
una restauración completa. No se cambian credenciales ni se envían mensajes.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.31-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.31-' + uuid.uuid4().hex[:8] + '.dump')
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

Sigue 2.32: consumidor/transporte simulado, revalidación y errores/reintentos.
Antes de producción: claves externas/rotación/retención operativas, mantenimiento,
proveedor/remitente/DNS/HTTPS y flujos completos MFA/recuperación/HTTP/UI. Sin compras,
VPS o DNS modificados. SEC-2026-001 permanece con vencimiento 2026-10-31 00:00 UTC;
no se amplía la excepción ni se declara cero vulnerabilidades.
