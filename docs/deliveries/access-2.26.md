# Acceso 2.26: pendientes MFA internos

2026-10-17, America/El_Salvador. Entrega interna; login operativo sin MFA.

## Comportamiento y límites

0013 crea `mfa_pendings`: usuario global, propósito cerrado `login_totp` o
`enrollment`, revisión, huella de contraseña, hashes separados de token y
navegador y fechas. Tokens aleatorios de 256 bits, representados con SecretStr;
no se almacenan tokens/cookies en claro. Sin empresa, rol o permisos comerciales.
El binding debe ser una cookie aleatoria emitida/verificada por el futuro adaptador,
no un valor del body. Validar formato no acredita su procedencia.

Emisión privada solo después de verificar contraseña con el mismo usuario
bloqueado; requiere revisión esperada actual. El llamador de confianza posee la
transacción y publica el token después del commit. Esta primitiva no verifica
por sí sola contraseña, factor, buzón o elegibilidad de incorporación.

Consumo/rotación comprueban estado activo, navegador, propósito, revisión,
contraseña y reloj leído después de locks. Plazo exclusivo: cinco minutos para
login y quince para incorporación desde contraseña. Rotar consume el token viejo
y preserva hora/plazo; no cambia propósito. Un propósito/navegador incorrecto no
consume el pendiente válido. Consumo devuelve contexto restringido, nunca una
prueba transferible o sesión: debe componerse con la transición autorizada dentro
de la misma transacción; un fallo posterior exige rollback.

Revocaciones globales existentes cierran también estos pendientes. Orden:
usuario -> selección heredada -> pendientes MFA -> sesiones. El runtime no puede
borrar filas; downgrade rechaza incluso filas consumidas. Los CHECK limitan forma
y ventanas; no sustituyen autorización ni impiden a SQL privilegiado cambiar
campos coherentemente. No hay activación/backfill, endpoint, cookie nueva o UI.
Selección MFA, recuperación, cancelación por navegador/logout HTTP y composición
con cuota/consumo de factor todavía pendientes.

## Archivos completos modificados

- `backend/app/modules/access/mfa_pending.py`: primitivas privadas.
- `backend/app/modules/access/models.py`: modelo con restricciones.
- `backend/app/modules/access/login_challenges.py`: cierre global de pendientes MFA.
- `backend/migrations/versions/0013_mfa_pendings.py`: migración y downgrade protegido.
- `backend/tests/integration/test_mfa_pending.py`: pruebas reales en PostgreSQL aislado.
- `backend/tests/integration/test_mfa_consumption.py`, `test_postgres.py`, `test_user_provisioning.py`: head esperado 0013.
- README, aceptación, decisiones, modelo, contratos MFA y revisión de seguridad.

## Comandos y resultados

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.26-ci.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.26-ci-retry.log 2>&1
```

Primera ejecución: CI exit 1 porque la auditoría Python devolvió exit 2 incompleto
por conectividad, antes de construir/ejecutar la aplicación de pruebas. Informe:
`.local/security-reports/3c6c444e1c0845cfb5b4230118a405d5.json`.
Un diagnóstico posterior manteniendo controles pasó: 70 dependencias Python,
ninguna omitida. Informe `.local/security-reports/267987c865e14e12971b5cd366bc28e6.json`.
No se modificaron escáneres, timeouts, dependencias o excepciones para continuar.
Npm conserva las cuatro entradas de SEC-2026-001, no cero hallazgos.

Segundo CI: **exit 0**, proyecto `carrillo-ci-95ba53352775`. **1.155 casos**:
960 backend (21 nuevos), 7 proxy, 152 frontend y 36 scripts. Lint/formato
(89 archivos Python), tipos, builds, roundtrip de migraciones/check, HTTP smoke
y limpieza pasaron. Seguridad obligatoria passed/accepted; informe privado
`.local/security-reports/fb979aa160dc443788419b2c23788ea9.json`.

Las 21 pruebas nuevas ejercitan aislamiento por propósito/navegador, hashes sin
tokens en claro, límites exactos/regresión de reloj, plazo original tras rotar,
cambio de contraseña/revisión/actividad, cierre global, rollback del consumo y
rotación, formato inválido, rechazo de borrado/extensión de plazo, downgrade
protegido y dos conexiones contendiendo realmente por el lock con un solo éxito.
No hay pruebas end-to-end de MFA ni una verificación de buzón implementada.

## Ejecución local

Comandos desde la raíz; detenerse si falla el backup. La copia verifica el índice,
no una restauración completa. No se regeneran credenciales, claves ni factores.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.26-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.26-' + uuid.uuid4().hex[:8] + '.dump')
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

Copia privada previa `.local/development/backups/pre-access-2.26-e70adda5.dump`:
exit 0, archivo 0600, índice verificado; sin ensayo de restauración completa.
Build del backend y migración local terminaron con exit 0.
Arranque/recarga, smoke, current/check y consulta de servicios: exit 0.
Head local `0013_mfa_pendings`; check sin operaciones nuevas. Cuatro servicios
saludables. Smoke verifica HTML español, 10 assets, mismo origen, 404, cabeceras
y rechazo sin Origin/CSRF. Sin cambios de credenciales o cuentas reales.

## Riesgos y siguiente tarea

MFA sigue sin activar; no se declara preparación productiva. Correo, clave
operativa/rotación, recuperación, retención, HTTPS y restauración segura siguen
pendientes. La excepción npm existente vence 2026-10-31 00:00 UTC, sin ampliación.
Siguiente 2.27: componer pendiente, cuotas y consumo TOTP en una sola transacción,
antes de integrar pruebas persistidas/guardias de sesión y HTTP/UI.
