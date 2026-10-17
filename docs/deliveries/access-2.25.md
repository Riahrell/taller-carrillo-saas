# Acceso 2.25: estado global de seguridad

2026-10-17, America/El_Salvador. Implementación interna; MFA aún no está activo.

## Cambios y contrato

0012 añade a users una revisión global inicialmente cero y una obligación MFA
inicialmente falsa. No verifica correos ni activa factores por backfill. El trigger
rechaza retrocesos, borrar una obligación establecida o cambiarla sin aumentar
revisión; no sustituye autorización de servicios ni protege frente al propietario.

`security_state.py` ofrece lectura bloqueada, comparación con revisión esperada,
avance y sincronización de obligación por membresía admin activa en tenant activo
o factor vigente. La obligación queda persistente aunque desaparezca ese motivo.
El llamador obtiene IDs/revisión de estado confiable, autoriza y posee la transacción.
No exponer mutaciones o flags al cliente. Orden usuario -> pendientes -> sesiones;
la sincronización no adquiere locks de tenant después del usuario. Las futuras
altas/promociones/factores deben invocarla dentro de sus protocolos transaccionales;
esta consulta sola no elimina carreras con cambios de roles aún no integrados.

Cada avance incrementa una vez, cierra pendientes de selección y revoca sesiones
de todas las empresas; inserta `security_revision_advanced` en el mismo commit.
El evento tiene sujeto/revisión, sin factor ficticio ni secretos. MFA mantiene sus
eventos anteriores con factor obligatorio; CHECK y FK distinguen ambos casos.
App continúa sin UPDATE/DELETE/TRUNCATE en eventos. Downgrade rechaza estado no
neutral/evidencia nueva. SQL directo autorizado podría subir revisiones sin pasar
por el servicio: el trigger no acredita auditoría o revocación de esa ruta.

No se conecta todavía a login, promoción o factor: las pruebas futuras deberán
persistir/comprobar revisión y demás evidencias en todos los caminos. La revisión
sola no es autenticación. Integrar hooks y pendientes sigue pendiente; no se
declaran las cookies actuales protegidas por una revisión que aún no consumen.

## Archivos completos

- `backend/app/modules/access/security_state.py`: servicios internos.
- `backend/app/modules/access/models.py`, `backend/migrations/versions/0012_user_security_state.py`: columnas, trigger y eventos tipados.
- `backend/tests/integration/test_security_state.py`: estado, revocación global, obligación, rollback, auditoría y concurrencia.
- `backend/tests/integration/test_mfa_consumption.py`, `test_postgres.py`, `test_user_provisioning.py`: head esperado 0012.
- README y documentación de estado/aceptación/seguridad: avance y límites.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.25-ci.log 2>&1
```

Resultados reales: CI exit 0 (`carrillo-ci-6dcda3f78070`), **1.134 casos acumulados**:
939 backend (19 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato (86
archivos), workflow, tipos, builds, migraciones/check, HTTP smoke y limpieza
pasaron. Sin UI nueva o recorrido manual de cuentas reales.

Los tests cubren revocación de dos empresas y pendientes, un solo ganador entre
revisiones concurrentes, obligación persistente, rollback de estado/eventos y
fallo real de permisos de auditoría. El trigger se ejercita con el rol runtime.
Migración vacía roundtrip y downgrade protegido pasaron. No hay claims de
restauración ante caída física durante COMMIT o de hooks todavía no integrados.

Seguridad durante CI: canary/secretos/historial/Python passed; npm accepted con las mismas
cuatro entradas SEC-2026-001. Informe CI privado:
`.local/security-reports/18ce8f0e6e3e4c07a588ae2bbc91be54.json`.

La auditoría adicional de cierre y su reintento terminaron con exit 2:
Python incompleto, no un resultado limpio. El diagnóstico conservando todos los
controles confirmó `ConnectionError`/timeout, salida sin JSON válido. Secretos y
canary pasaron; npm conservó la excepción existente. No se cambiaron dependencias
ni se relajaron controles. Último informe privado:
`.local/security-reports/712d06680f10404dbdc407ff82095bb9.json`.
Registro diagnóstico sin salida cruda:
`/tmp/carrillo-access-2.25-security-diagnostic.log`.
Revalidar cuando esté disponible el servicio externo:

```bash
python3 -m scripts.security > /tmp/carrillo-access-2.25-security.log 2>&1
```

## Ejecución local

Copia previa verificada, exit 0:
`.local/development/backups/pre-access-2.25-a22db712.dump`, 0600, fuera de Git/build.
Índice comprobado; no se ensayó restauración completa. Sin regenerar credenciales.
Comandos desde la raíz (detenerse si la copia falla):

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.25-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.25-' + uuid.uuid4().hex[:8] + '.dump')
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

Resultados locales reales: build, copia, migración, arranque y recarga exit 0.
Alembic current: `0012_user_security_state (head)`; check sin nuevas operaciones.
Smoke exit 0: HTML, 10 assets, mismo origen, 404, cabeceras y rechazo sin
Origin/CSRF. Los cuatro servicios quedaron saludables. MFA no se activó y las
credenciales existentes no se modificaron.

## Riesgos y siguiente paso

MFA operativo, pendientes tipados, hooks de roles/factores, correo/recuperación,
revisión en todas las pruebas de sesión, HTTPS, restauración y retención siguen
pendientes. SEC-2026-001 npm permanece vigente hasta 2026-10-31 UTC, sin ampliar
la excepción. La auditoría Python de cierre sigue incompleta por conectividad,
aunque pasó dentro del CI anterior. No se declara preparación productiva ni
certificación.

Siguiente 2.26: pendientes MFA tipados ligados a navegador, propósito, revisión y
caducidad, sin sesión operativa antes de completar pruebas.
