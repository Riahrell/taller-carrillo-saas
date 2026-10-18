# Entrega 2.37 — Evidencia explícita del correo para futuro reset

Fecha: 2026-10-17. Caso interno pequeño: persistir elegibilidad del buzón al
confirmar incorporación MFA. No hay reset operativo, endpoints/formulario nuevos,
proveedor real, envío externo, cambios de cuentas reales o acciones sobre VPS.

## Resultado y límites

Una confirmación nueva de contraseña/navegador/token de buzón registra evidencia
de la dirección exacta en el mismo commit que el consumo, permiso restringido,
descarte de ciphertext de outbox y evento de confirmación existente. Migración
0023 sin backfill; emitir, inspeccionar o aceptar transporte no verifica correo.
Se conserva el plazo original; si vence tras escribir, todo se revierte.

verified_mailboxes guarda propietario, desafío fuente, hash separado por dominio/
usuario/dirección, fecha de verificación e invalidación. FK compuesta evita copiar
el origen de otro usuario; solo una evidencia vigente por usuario. No guarda un
token o dirección adicional en claro. Una confirmación posterior conserva historia
e invalida evidencia anterior. Runtime sin DELETE y downgrade con datos rechazado.

El lector interno toma locks usuario → desafío → evidencia y revalida usuario
activo, dirección, fecha y origen confirmado. No da permiso de reset, sesión,
empresa o rol: su resultado solo sirve en la transacción de un futuro emisor.
No reactivar cuentas/membresías; una membresía inactiva no borra identidad global.
La prueba histórica del buzón sobrevive cierre/caducidad del desafío y cambios de
contraseña/revisión; el reset deberá probar acceso fresco mediante token propio.

Un trigger invalida evidencia vigente en la transacción de cambio de users.email,
incluso UPDATE directo. Volver al correo anterior no revive la prueba; rollback
del cambio también revierte la invalidación. No se implementa cambio de correo
operativo, sus revocaciones/avisos o defensa frente a SQL privilegiado. No es
borrado seguro de memoria, backups o WAL ni política final de retención.

Esta fuente cubre nuevas confirmaciones de incorporación MFA. Falta verificación
general ligada a sesión para cuentas sin MFA; no se consideran verificadas por
existir. Factor y códigos MFA no se modifican. El permiso de incorporación sigue
con su propósito/plazo y nunca se convierte en token de reset.

## Archivos completos modificados

- backend/app/modules/access/mailbox_verification.py: registro y lectura internos.
- backend/app/modules/access/enrollment_email.py: composición y plazo final.
- backend/app/modules/access/models.py: evidencia/FK/índice/restricciones.
- backend/migrations/versions/0023_verified_mailboxes.py: tabla/trigger/downgrade.
- backend/tests/integration/test_enrollment_email.py: 19 casos y limpieza efímera.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py y test_user_provisioning.py: head 0023.
- README; docs/email-recovery-design.md, access-security-closure.md, decisions.md,
  data-model.md, acceptance.md, security-review.md, mfa-persistence-contract.md
  y esta entrega.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.37-ci.log 2>&1
```

CI **exit 0**, proyecto `carrillo-ci-41373185ac98`: **1.440 casos** = 1.245
backend (19 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato (115 archivos),
tipos, builds, migraciones y smoke HTTP pasaron; proyecto aislado limpiado.
Informe privado saneado de seguridad:
`.local/security-reports/0cdfd014938842188fc51cd144fecc08.json`.
Escaneos passed/accepted; npm conserva cuatro entradas SEC-2026-001.

Casos nuevos: confirmación explícita, emisión/inspección sin elegibilidad,
propietario/hash/dirección/fechas correctos, ausencia de sesión/factor nuevo,
persistencia a través del ciclo de autenticación, cinco rechazos de evidencia,
cambio/reversión/rollback de correo, fallo SQL real de INSERT/commit/plazo final,
restricciones DB/retención/downgrade, renovación de evidencia y confirmación que
espera un cambio de correo concurrente. Regresión de concurrencia del mismo token
ya existente incluida. PostgreSQL e identidades sintéticas; no envío real o caída
física de COMMIT. No atribuir seguridad completa a resultados de componentes.

## Ejecución local

Ejecución **exit 0**: imagen backend construida; copia privada previa
`.local/development/backups/pre-access-2.37-78cf8750.dump` (0600, 68.647 bytes,
índice verificado; no restauración completa). Migración
`0023_verified_mailboxes (head)` aplicada, Alembic check sin operaciones nuevas,
smoke correcto con 10 assets y backend/DB/frontend/proxy saludables. Logs:
`/tmp/carrillo-access-2.37-dev-build.log`, `/tmp/carrillo-access-2.37-backup.log`,
`/tmp/carrillo-access-2.37-migrate.log`, `/tmp/carrillo-access-2.37-local.log`.
No se verifican correos ni se cambian credenciales de usuarios operativos.

Comandos exactos; detenerse ante fallo de copia/CI. El índice verificado no es
una restauración completa.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.37-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.37-' + uuid.uuid4().hex[:8] + '.dump')
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

Sigue emisión/consumo transaccional del reset para buzones elegibles, con propósito
de token propio, cuotas y staging cifrado, conservando factor/códigos MFA. La
verificación general para cuentas sin MFA también sigue pendiente, al igual que
avisos, reautenticación, hooks de roles/último admin, HTTP/UI y operación/HTTPS/
claves/restauración. No activar factores reales antes de cerrar los recorridos.
SEC-2026-001 conserva cuatro entradas npm y vence 2026-10-31 00:00 UTC, sin
extenderse ni afirmar cero vulnerabilidades. Ver access-security-closure.md.
