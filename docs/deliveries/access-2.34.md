# Acceso 2.34: inicio restringido de recuperación MFA offline

2026-10-17, America/El_Salvador. Avanza ciclo de recuperación del
[plan de cierre](../access-security-closure.md). Interno, sin HTTP/UI, proveedor,
claves operativas, factores reales, DNS/VPS o reglas comerciales modificadas.

## Comportamiento

Begin exige pendiente LOGIN_TOTP con contraseña verificada y navegador correctos;
resuelve usuario/factor/conjunto en servidor. No recibe esos IDs del solicitante.
Cuotas MFA IP -> cuenta antes de usuario -> pendiente -> factor -> conjunto -> código.
Formato/código incorrecto confirma fallos compartidos; éxito no limpia cuotas.
Leer reloj tras locks/consumo y comprobar plazo de contraseña antes y después
permite revertir todo si vence durante el trabajo.

Consumir un código offline, revocar sesiones/pendientes globales, cerrar permisos
anteriores, crear permiso y auditar mfa_recovery_consumed/mfa_recovery_started son
un commit. Factor y conjunto anteriores siguen activos; otros códigos sin usar
se conservan. No cambiar revisión hasta reemplazo confirmado. Permiso opaco de
256 bits con hash de propósito distinto, navegador por su pendiente de origen,
quince minutos desde emisión. Contraseña tenía menos de cinco minutos al emitir;
no prolongar por inspección/uso. No concede empresa, sesión ni alta inicial.

Inspección ligada al navegador es informativa y sin mutación; relee revisión,
contraseña/actividad, factor/conjunto vigentes, código consumido y etapa de origen.
El consumidor privado marca uso y devuelve contexto solo dentro de una transacción
confiable: el futuro setup sustituto debe crearse en el mismo commit y revalidar
la prueba original al activar. Esta entrega NO crea setup ni reemplaza factor.
No emitir contexto como prueba HTTP o de negocio.

Una sesión normal con prueba MFA persistida cierra recuperaciones antes de insertar
sesión, en el mismo commit. Verificar solo TOTP/crear prueba temporal no basta.
Rollback de sesión conserva permiso y no devuelve el código offline. Revocación
global y reinicio del mismo navegador también cierran; otro navegador por sí solo
no. Si se pierde respuesta, exigir nueva verificación de contraseña y otro código sin
usar. No reponer códigos por cancelación ni reabrir tokens viejos.

FK/check/unicidad acotan forma y propietarios; servicio revalida conjunto/código
vigentes y su momento de consumo. Downgrade con cualquier permiso/evento nuevo,
aunque usado/cerrado/caducado, se rechaza. Runtime sin DELETE; no backfill o cambios
de cuentas existentes. No es defensa frente a escrituras SQL privilegiadas.

## Archivos completos modificados

- backend/app/modules/access/mfa_recovery.py: inicio/inspección y consumidor privado.
- backend/app/modules/access/recovery_state.py: cierre compartido sin ciclos de importación.
- backend/app/modules/access/models.py y backend/migrations/versions/0020_mfa_recovery_permits.py.
- backend/app/modules/access/login_challenges.py, mfa_pending.py, sessions.py: cancelación/revocación atómica.
- backend/tests/integration/test_mfa_recovery.py: identidades y códigos sintéticos.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py, test_user_provisioning.py: head 0020.
- README, cierre de seguridad, contratos, aceptación, modelo, decisiones y revisión.

## Validación

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.34-ci.log 2>&1
```

CI **exit 0**, proyecto `carrillo-ci-29740f8e9b80`: **1.378 casos**, 1.183
backend (32 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato (109 archivos),
tipos, builds, roundtrip/check Alembic, HTTP smoke y limpieza pasaron.
Auditorías obligatorias passed/accepted; informe privado: `.local/security-reports/73811fa466d04b118004bf053b70689d.json`.
Npm conserva cuatro entradas SEC-2026-001, sin ampliar vencimiento.
Casos nuevos: permiso sin negocio/alta inicial, consumo único y revocación global,
factor/conjunto conservados, formatos/cuota, origen/revisión/contraseña actual,
plazos exactos, rollback por SQL/commit/expiración, cancelación atómica de sesión,
reinicio con otro código, contexto privado de un uso, dos entradas realmente
bloqueadas con un éxito y controles DB/downgrade. Pruebas con PostgreSQL efímero, claves,
contraseñas/códigos sintéticos. Sin correo, sustitución, red de proveedor, QR/HTTPS,
recepción de respuestas ni caída física de COMMIT probados. No declarar MFA público
ni recuperación completa. Conservar evidencia de todos los intentos de CI.

## Ejecución local

Ejecución local **exit 0**: imagen backend construida, copia previa privada
`.local/development/backups/pre-access-2.34-b8ee3ea0.dump` (0600, índice verificado;
sin restauración completa), migración `0020_mfa_recovery_permits (head)` aplicada.
Alembic check sin operaciones nuevas, smoke web correcto con 10 assets y backend,
DB, frontend y proxy saludables. No se crean permisos en cuentas reales ni se
habilitan rutas de recuperación. Logs: `/tmp/carrillo-access-2.34-dev-build.log`,
`/tmp/carrillo-access-2.34-migrate.log`, `/tmp/carrillo-access-2.34-local.log`.

Desde la raíz; detenerse si falla el backup. El índice verificado no equivale a
una restauración completa. No se cambian credenciales ni se envían mensajes.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.34-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.34-' + uuid.uuid4().hex[:8] + '.dump')
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

Sigue setup sustituto y reemplazo con TOTP nuevo: invalidar factor/conjunto viejo,
generar diez códigos, avanzar revisión/revocar y auditar en un commit. Faltan además
avisos de seguridad en cola, contraseña olvidada/correo elegible, hooks de roles/
último admin, reautenticación, HTTP/UI y operación; ver criterios de cierre.
No habilitar entrada HTTP hasta completar reemplazo. Retención de evidencias y
mantenimiento programado siguen pendientes. SEC-2026-001 conserva cuatro entradas
npm aceptadas hasta 2026-10-31 00:00 UTC; sin ampliar ni declarar cero hallazgos.
