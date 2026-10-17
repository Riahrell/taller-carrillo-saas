# Acceso 2.28: sesiones desde evidencia MFA persistida

2026-10-17, America/El_Salvador. Servicio interno; sin login MFA HTTP/UI activo.

## Comportamiento

MfaSessionIssuer selecciona una membresía candidata desde login_verified y binding
opacos. No acepta usuario, empresa, rol o flag MFA del cliente. Revalida prueba,
navegador, usuario/revisión/contraseña/factor y membresía/empresa actuales bajo
lock del usuario. Un intento ligado a navegador válido consume la prueba aunque
la membresía sea ajena/inactiva; contexto inválido no emite sesión. Navegador o
etapa incorrectos no consumen la prueba legítima.

Cierre de prueba y sesión confirman en un commit; cookie solo después. La sesión
referencia esa prueba con FK y unicidad. Creación coincide con consumo y debe
estar dentro del límite original de cinco minutos. No se traslada un resultado
de verificación aislado entre transacciones para autorizar sesión. Rollback de
esta fase conserva la prueba ya emitida por 2.27; no deshace un TOTP consumido
en el commit anterior. Falta de permisos/commit/reloj no publica éxito.

Emisión central heredada rechaza factor activo u obligación persistida. Login
heredado tampoco revela empresas o emite selección para esas cuentas; selección
vieja no elude la guardia. El kernel de obligación admin por rol y sus hooks no
se activan todavía: no declarar MFA obligatorio completo para administradores.

Resolución exige revisión actual. Una sesión con prueba revalida propietario,
propósito, huella de contraseña, factor vigente y tiempos de emisión/consumo.
Vida de ocho horas, inactividad de treinta minutos; el límite de cinco minutos
aplica a emitir, no a toda lectura posterior de sesión. La confirmación reciente
MFA vence a los cinco minutos desde la prueba más antigua y no se renueva al
seleccionar empresa o tocar actividad. Reautenticación heredada por contraseña
no renueva MFA; queda pendiente su variante completa.

0015 agrega revisión nullable y referencia única a prueba. **Cookies anteriores
sin revisión dejan de servir: iniciar sesión otra vez.** Sin backfill, cambios
de credenciales, factor real o activación pública. Downgrade rechaza registros
con evidencia nueva incluso revocados, cualquier factor o estado de usuario no neutral; no borrar para forzarlo. Retención futura
debe respetar FK sesión/prueba. Restricciones y guardias no protegen frente al
propietario SQL; no hay evaluación/certificación ASVS completa.

## Archivos completos modificados

- `backend/app/modules/access/mfa_sessions.py`: emisor con transacción propietaria.
- `backend/app/modules/access/sessions.py`: persistencia y guardias centrales.
- `backend/app/modules/access/authentication.py`: rechazo de rutas heredadas sin factor.
- `backend/app/modules/access/reauthentication.py`: ventana reciente desde prueba MFA.
- `backend/app/modules/access/models.py`, `backend/migrations/versions/0015_mfa_sessions.py`: revisión/FK/unicidad.
- `backend/tests/integration/test_mfa_sessions.py`: integración PostgreSQL.
- `backend/tests/integration/test_security_state.py`: sesiones históricas sintéticas anteriores al factor, sin bypass runtime.
- `backend/tests/integration/test_mfa_verification.py`: contraseña válida con factor no abre selección ni borra cuotas.
- `backend/tests/integration/test_reauthentication.py`, `test_user_provisioning.py`: pruebas históricas con fixtures anteriores a 0015 y rechazo de downgrade con evidencia nueva.
- `backend/tests/integration/test_mfa_consumption.py`, `test_postgres.py`, `test_user_provisioning.py`: head esperado 0015.
- README, modelo, decisiones, aceptación y contratos/revisión de seguridad.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.28-ci.log 2>&1
```

La primera ejecución detectó dos pruebas históricas que intentaban atravesar
el nuevo bloqueo de downgrade: exit 1, 1.014 backend pasados y dos fallidos. Se
adaptó la preparación de datos históricos únicamente en la DB efímera; no es
una receta de rollback operativo. Una ejecución intermedia conservó aún una
fixture antigua (1.015 backend pasados, un fallo); se corrigió y se reejecutó
todo. El nuevo estado debe seguir bloqueando la
vuelta a software que ignore MFA. Se conservó esa protección y se añadieron
casos sin sesiones previas. La validación final incluye todos los cambios:

```bash
python3 -m scripts.ci > /tmp/carrillo-access-2.28-ci-final.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.28-ci-verified.log 2>&1
```

CI final **exit 0**, proyecto `carrillo-ci-b2c1beae7492`: **1.214 casos**,
1.019 backend (33 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato
(94 archivos), tipos, builds, roundtrip/check de migraciones, HTTP smoke y limpieza
pasaron. Informe privado de auditorías passed/accepted:
`.local/security-reports/315d6c0aef334d18bbe11c96a2ab34b2.json`.
Npm conserva las cuatro entradas SEC-2026-001; no son cero hallazgos.

Los casos nuevos ejercitan emisión/contexto/propósito/binding y replay, plazo
original y confirmación reciente, todas las vías heredadas, cambio de identidad,
contraseña/revisión/factor/contexto, cookies sin revisión, traslado a otro dueño,
expiración después de releer contexto, fallos reales de permisos/commit, ausencia
de renovación MFA por contraseña, unicidad real en DB, dos conexiones realmente
bloqueadas y downgrade protegido incluso sin sesiones ni factor en los casos de
estado de usuario. No hay MFA end-to-end de navegador o caída física de COMMIT.

Resultados locales: build, copia previa, migración, arranque/recarga, smoke,
current/check y consulta de servicios terminaron con exit 0. Backup privado
`.local/development/backups/pre-access-2.28-b6db3f0c.dump`, modo 0600, índice
verificado; no restauración completa ensayada. Head `0015_mfa_sessions`, sin
nuevas operaciones en check. Cuatro servicios saludables. Smoke verificó HTML
español, 10 assets, mismo origen, 404, cabeceras y rechazo sin Origin/CSRF.
Sin cambios de credenciales/usuarios/factores reales. Cookies antiguas sin revisión
requieren login nuevo. Registro local `/tmp/carrillo-access-2.28-local.log`.

## Ejecución local

Desde la raíz; detenerse si falla el backup. La copia comprueba índice, no una
restauración completa. No se regeneran credenciales o factores. Se exige login
nuevo a cookies sin revisión de seguridad.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.28-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.28-' + uuid.uuid4().hex[:8] + '.dump')
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

MFA público/incorporación, correo, recuperación, hooks de roles/factores y último
admin elegible, reautenticación MFA, cancelación, HTTPS, retención y respaldo de
claves siguen pendientes. SEC-2026-001 conserva cuatro entradas npm aceptadas
hasta 2026-10-31 00:00 UTC sin extensión; no cero hallazgos ni preparación productiva.
Siguiente 2.29: inicio de login interno con pendientes MFA, antes de HTTP/UI.
