# Acceso 2.35: reemplazo MFA por recuperación offline

2026-10-17, America/El_Salvador. Completa el reemplazo interno del
[plan de cierre](../access-security-closure.md), sin HTTP/QR/UI, correo real,
claves/factores de cuentas operativas, DNS/VPS ni reglas comerciales nuevos.

## Comportamiento

Begin intercambia el permiso offline una vez por setup cifrado con token propio.
Usuario y factor provienen de estado validado en servidor. UUID reservado del
sucesor y AAD usuario/UUID, sin ID de factor o rol elegidos por el cliente. Plazo
original del permiso, nunca quince minutos nuevos desde QR/setup. Semilla solo
tras commit y sin relectura; factor/conjunto actuales aún activos.

Activate toma cuotas IP -> cuenta, relee el origen completo del permiso bajo
usuario -> pendiente -> factor -> conjunto -> código -> permiso -> setup.
Acceso por ID al permiso es privado y únicamente detrás del token/binding de
setup, no una alternativa de autenticación. Exige código offline consumido,
revisión/contraseña vigentes, mismo navegador y factor/conjunto aún activos.
Verifica TOTP nuevo con perfil existente; fallos gastan cuota compartida, éxito
no la borra. Claves ausentes/AEAD alterado fallan cerrados.

TOTP correcto retira factor y conjunto anteriores (incluidos sus códigos sin usar),
crea sucesor con primer contador/hora consumidos y exactamente diez hashes nuevos,
cierra/limpia setup, avanza revisión/revoca acceso global y audita mfa_replaced en
un commit. Flush del retiro antes de insertar sucesor respeta índice parcial;
no hay commit intermedio. Historial anterior conservado. Semilla reencriptada
con clave activa. Código/hash ligado a sujeto y conjunto; no códigos en claro en DB.

Bundle de códigos solo después de commit y una vez. No login automático: nuevo
login completo con un OTP no consumido. Perder respuesta tras commit no autoriza
releer códigos; necesita factor actual y futura regeneración segura. Perder setup
requiere nueva contraseña verificada y otro código sin usar. Rollback de intento
no repone el código que ya se consumió al iniciar recuperación.

Sesión MFA normal, reinicio del propio navegador o revocación global cierran setup
y retiran ciphertext en la misma transacción. Purga acotada de caducados explícita,
sin schedule; no toma locks de ancestros. Metadatos retenidos y downgrade con
cualquier setup/evento rechazado. No es borrado seguro de memoria/backups/WAL ni
defensa contra SQL privilegiado. No implementa rotación voluntaria del factor,
regeneración del conjunto con el mismo factor o recuperación excepcional.

## Archivos completos modificados

- backend/app/modules/access/mfa_replacement.py: setup, confirmación y purga.
- backend/app/modules/access/mfa_recovery.py: revalidación privada de origen consumido.
- backend/app/modules/access/recovery_state.py: cierre/limpieza de setup al cancelar permiso.
- backend/app/modules/access/models.py y backend/migrations/versions/0021_mfa_replacement.py.
- backend/tests/integration/test_mfa_recovery.py: sustitución y regresión de recuperación.
- backend/tests/integration/test_postgres.py, test_mfa_consumption.py, test_user_provisioning.py: head 0021.
- README, cierre de seguridad, contratos, aceptación, modelo, decisiones y revisión.

## Validación

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.35-ci.log 2>&1
```

CI **exit 0**, proyecto `carrillo-ci-669992c7095c`: **1.404 casos**, 1.209
backend (26 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato (111 archivos),
tipos, builds, roundtrip/check Alembic, HTTP smoke y limpieza pasaron.
Auditorías obligatorias passed/accepted; informe privado: `.local/security-reports/5071bc4e5dab43d2aae116fea1dadedc.json`.
Npm conserva cuatro entradas SEC-2026-001, sin ampliar vencimiento.
Casos nuevos: semilla/permiso de uso único y plazo original, sustitución completa,
vínculo de diez hashes, invalidación del conjunto viejo, primer OTP consumido y
login posterior completo con nuevo factor; cuota, origen cambiado, cancelación,
purga, rollback real SQL/commit/AEAD/clave/caducidad final, dos sustituciones
realmente bloqueadas con un éxito y controles DB/retención/downgrade. Claves/semillas/usuarios sintéticos en DB
PostgreSQL efímera; no se cambian factores reales ni se prueban teléfonos, entrega
de respuestas, SMTP, QR/HTTPS públicos o caída física de COMMIT. No afirmar MFA
público o seguridad cerrada; conservar resultados de cada ejecución.

## Ejecución local

Ejecución local **exit 0**: imagen backend construida, copia previa privada
`.local/development/backups/pre-access-2.35-b3d89ae4.dump` (0600, índice verificado;
sin restauración completa), migración `0021_mfa_replacement (head)` aplicada.
Alembic check sin operaciones nuevas, smoke web correcto con 10 assets y backend,
DB, frontend y proxy saludables. No se ejecuta reemplazo sobre usuarios reales.
Logs: `/tmp/carrillo-access-2.35-dev-build.log`, `/tmp/carrillo-access-2.35-migrate.log`,
`/tmp/carrillo-access-2.35-local.log`.

Desde la raíz; detenerse si falla el backup. El índice verificado no equivale a
una restauración completa. No se cambian credenciales ni se envían mensajes.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.35-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.35-' + uuid.uuid4().hex[:8] + '.dump')
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

Sigue regeneración segura de códigos offline con contraseña/TOTP actuales frescos,
para resolver pérdida de la entrega sin cambiar el factor ni revelar códigos viejos.
Pendientes: avisos de seguridad, reset de contraseña/correo elegible, hooks de roles/
último admin, reautenticación, HTTP/UI y operación; ver criterios de cierre.
No activar factores operativos antes de esos recorridos. Retención/purga programada
pendientes. SEC-2026-001 conserva cuatro entradas npm hasta 2026-10-31 00:00 UTC,
sin ampliar ni declarar cero vulnerabilidades.
