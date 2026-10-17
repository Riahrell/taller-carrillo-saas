# Acceso 2.32: consumidor de correo MFA con transporte simulado

2026-10-17, America/El_Salvador. Continúa seguridad antes de inventario, según el
usuario. [Criterios de cierre](../access-security-closure.md). No SMTP/HTTP nuevo,
worker programado, proveedor, compras, DNS, VPS o claves operativas modificados.

## Comportamiento y aceptación

Un intento acotado por llamada, hasta veinte candidatos; salta usuarios ocupados
con SKIP LOCKED. Orden usuario -> pendiente -> desafío -> outbox -> entrega.
Semántica de bloqueo según [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html).
Valida usuario activo, obligación/ausencia de historial de factor, propósito,
revisión, huella de contraseña/dirección, pendiente/desafío abiertos, caducidad
y ciphertext/token/destinatario. Lease de treinta segundos o caducidad original,
lo que ocurra antes. Commit de lease, nueva revalidación y commit antes de llamar
al transporte inyectado, fuera de transacciones. Sin secretos en resultados/errores.

Aceptación del transporte no es confirmación de buzón, sesión o autenticación.
Solo NOT_SENT explícito permite reintento (sesenta segundos, máximo tres), siempre
con misma identidad de mensaje y sin renovar caducidad/cuotas. Rechazo permanente
cierra. Excepciones, respuestas desconocidas/tardías y leases abandonados quedan
inciertos terminales, sin reenvío automático. Ciphertext se descarta al terminar.
Si no alcanza el plazo para reintentar, se cierra sin enviar de nuevo.

No promete exactamente-una-entrega ni entrega a bandeja de entrada. Si el proceso
muere después de claim, puede perder un mensaje aún no enviado; se elige no
reenviar automáticamente ante incertidumbre. Si falla commit después de envío,
queda leased y se reconcilia a incierto al caducar, sin volver a enviar. ACK viejo
no reabre estado cerrado. Se conserva último resultado/contador, no historial por
intento ni respuestas crudas del proveedor. SQL privilegiado puede falsificar
estado coherente; no se atribuye protección contra ello.

Existe carrera inevitable entre validación y efecto externo: revocar durante
la llamada puede dejar salir un token ya inválido. La prueba de buzón vuelve a
validar estado y no lo consume. No se mantienen locks de usuario durante red.
El transporte real futuro debe imponer deadline total dentro del lease, proteger
URL/payload y usar origen canónico HTTPS; este módulo síncrono no interrumpe un
adaptador que ignore deadline. No habilitarlo con proveedor antes de esa validación.
No hay scheduler/métricas operativas ni mantenimiento programado todavía.

## Archivos completos modificados

- backend/app/modules/access/mail_delivery.py: consumidor/protocolo internos.
- backend/app/modules/access/models.py y backend/migrations/versions/0018_mail_delivery.py: último lease/resultado tipado.
- backend/tests/integration/test_enrollment_email.py: transporte sintético, estados, concurrencia y fallos.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py y test_user_provisioning.py: head 0018.
- docs/access-security-closure.md: alcance pendiente y criterios verificables.
- README, decisiones, aceptación, modelo, contratos y revisión de seguridad.

## Validación

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.32-ci.log 2>&1
```

Primera ejecución **exit 1**: 1.116 backend pasaron y 12 fallaron porque el
transporte sintético exigía pool global en cero, ignorando la conexión de guardia
que mantiene db_session. Se corrigió para exigir el mismo número que la fixture
antes de ejecutar el consumidor: ninguna conexión adicional del dispatcher al
llamar transporte. No se cambia comportamiento productivo ni se quitan checks.

```bash
python3 -m scripts.ci > /tmp/carrillo-access-2.32-ci-final.log 2>&1
```

CI final **exit 0**, proyecto `carrillo-ci-c79481147dda`: **1.323 casos**,
1.128 backend (24 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato
(103 archivos), tipos, builds, roundtrip/check Alembic, HTTP smoke y limpieza
pasaron. Auditorías passed/accepted; informe privado
`.local/security-reports/a4110dff878b48d186e3e6612b585f81.json`.
Npm conserva cuatro entradas SEC-2026-001, sin ampliar vencimiento.
Los casos nuevos prueban resultados explícitos, transporte sin conexiones extra,
fuente cambiada/caducada, límite/demora de reintentos, lease abandonado, ACK viejo,
revocación antes/durante transporte, respuesta tardía, rollback en tres commits,
ciphertext alterado, consumidores concurrentes y controles DB/downgrade. Las llamadas son únicamente a un objeto
sintético en memoria, con usuarios y claves sintéticos en PostgreSQL efímero.
No se prueba proveedor, TLS SMTP, límites reales de red, recepción efectiva o
caída física de COMMIT. No afirmar MFA público o acceso seguro terminado.

## Ejecución local

Ejecución local **exit 0**: imagen backend construida, copia previa privada
`.local/development/backups/pre-access-2.32-a02ed638.dump` (0600, índice verificado;
sin restauración completa), migración aplicada a `0018_mail_delivery (head)`.
Alembic check sin operaciones nuevas, smoke web correcto con 10 assets y cuatro
servicios saludables (backend, DB, frontend y proxy). No se invoca el consumidor
con cuentas operativas ni se habilita un proceso de envío.
Logs locales: `/tmp/carrillo-access-2.32-dev-build.log`,
`/tmp/carrillo-access-2.32-migrate.log`, `/tmp/carrillo-access-2.32-local.log`.

Desde la raíz; detenerse si falla el backup. El índice verificado no equivale a
una restauración completa. No se cambian credenciales ni se envían mensajes.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.32-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.32-' + uuid.uuid4().hex[:8] + '.dump')
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

Sigue incorporación inicial TOTP con permiso de buzón y códigos offline, en una
transacción. Faltan recuperación, hooks de rol/factor/último admin, reautenticación,
HTTP/UI y operación/HTTPS/correo real; criterios en access-security-closure.md.
SEC-2026-001 sigue vigente hasta 2026-10-31 00:00 UTC, sin ampliar ni declarar
cero hallazgos. No se modifica seguridad para adelantar inventario.
