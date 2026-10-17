# Entrega: cuotas MFA independientes (acceso 2.24)

Fecha: 2026-10-17 (America/El_Salvador).
Rama: `codex/mfa-recovery-contract`.
**Fachada interna; login, selección y reautenticación siguen sin MFA activo.**

## Resultado

0011 crea `mfa_throttles`, separada de los contadores de contraseña. Cinco fallos
por UUID global de usuario y treinta por IP, ventana fija de quince minutos desde
el primer fallo; renovación en el límite exacto, sin ampliar la ventana al bloquear.
TOTP y recuperación comparten presupuesto entre workers/propósitos. IPv4 y su
representación IPv6 mapped comparten clave; hashes separados por dominio, sin
IP/correo en claro. El hash de IP no se presenta como anonimización.

`MfaVerifier` posee su transacción: confirma rechazos como fallos persistentes;
consumo válido/auditoría confirman juntos y el resultado solo sale tras commit.
Orden IP -> cuenta -> usuario -> factor/hijos. SQL parametrizado, UPSERT y locks
con refresh evitan perder incrementos. Un factor acertado conserva ambos
historiales: no es todavía autenticación completa. Contraseña exitosa o un
pendiente nuevo tampoco los reinician. No hay función de reset ni contador del
cliente. Los intentos bloqueados no evalúan el factor.

DB/clave/auditoría/timeout/reloj inválido fallan cerrados con mensaje genérico y
rollback, sin publicar éxito o cargar un código incorrecto. No se reintenta
commit incierto. App sin DELETE; downgrade rechazado ante cualquier cuota o
factor, incluso cero/expirado, para no olvidar estado. Sin backfill o factores
operativos nuevos, endpoints, frontend, claves o dependencias nuevas.

No emite sesión o permiso de recuperación. No usar el resultado en otra transacción
para crear sesión: al integrar pendientes/revisión, sus transiciones autorizadas
deben estar en la misma transacción propietaria. Los helpers de consumo de bajo
nivel siguen disponibles para composición interna/pruebas; no exponerlos en HTTP
saltando la fachada. Contrato completo: [verificación MFA](../mfa-verification-contract.md).

## Archivos completos creados o modificados

- `backend/app/modules/access/mfa_verification.py`: fachada, claves de cuota,
  renovación, locks, fallos persistentes y errores genéricos después de rollback.
- `backend/app/modules/access/models.py`,
  `backend/migrations/versions/0011_mfa_throttles.py`: tabla, restricciones,
  permisos y downgrade protegido.
- `backend/tests/integration/test_mfa_verification.py`: 34 casos nuevos para límites,
  ventanas exactas, mezcla de factores, nuevas IP/workers, concurrencia real,
  timeout, fallos de commit/auditoría/clave/reloj y contraseña/pendientes reales.
- `backend/tests/integration/conftest.py`: fixture MFA compartido, identidades
  sintéticas y limpieza solo mediante el propietario de la DB efímera protegida.
- `backend/tests/integration/test_mfa_consumption.py`: reutiliza el fixture movido;
  conserva los 75 casos existentes sin duplicarlos o sumar el movimiento como tests.
- `backend/tests/integration/test_postgres.py`,
  `backend/tests/integration/test_user_provisioning.py`: head esperado 0011.
- `README.md`, `docs/acceptance.md`, `docs/data-model.md`, `docs/decisions.md`,
  `docs/mfa-persistence-contract.md`, `docs/mfa-recovery-contract.md`,
  `docs/mfa-verification-contract.md`, `docs/reviews/plan-maestro-v1.1.md`,
  `docs/security-review.md` y este registro: estado, límites, evidencia y siguiente tarea.

Archivos completos en el repositorio. Sin implementación extensa de los flujos de
incorporación, recuperación, sesión o módulos comerciales en esta entrega.

## Comandos de validación

Desde la raíz del repositorio:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.24-ci-final.log 2>&1
python3 -m scripts.security > /tmp/carrillo-access-2.24-security-verified.log 2>&1
```

Resultados reales de la primera ejecución: exit 0, proyecto
`carrillo-ci-13228ed7d59c`, 1.113 casos (32 nuevos). Ruff/formato, workflow, tipos,
builds, migraciones, HTTP smoke y limpieza pasaron. Canary y secretos/Python
passed, npm accepted con las cuatro entradas SEC-2026-001; informe privado
`.local/security-reports/4279741050474d418e125e2d37edd191.json`.

La revisión final añadió normalización de zonas de interfaz IPv6 y dos casos
para impedir presupuestos distintos de la misma dirección. La ejecución final
sustituye, no suma, el recuento anterior. Resultado final: **exit 0**, proyecto `carrillo-ci-70c84af5737b`,
**1.115 casos únicos:** 920 backend (34 nuevos), 7 proxy, 152 frontend y 36 scripts.
Ruff/formato (83 archivos), workflow, tipos, builds, migraciones, HTTP smoke y
limpieza pasaron. Whitespace y 31 enlaces relativos comprobados sin errores. Seguridad: canary/secretos/Python passed, npm accepted con
SEC-2026-001; informe `.local/security-reports/f69063677669479ea179bed23750fe8c.json`.
Escaneo de cierre con fixture/documentación final: exit 0, mismos estados y
excepción; informe `.local/security-reports/c144ef9c4f234ea8b1fe800b4806f2f8.json`.

Se amplió el fixture sintético para evitar coincidencias aleatorias también en
los contadores futuros usados al probar quince minutos. Sin cambiar lógica de
runtime por ese ajuste, se ejecutaron las dos suites MFA en otro proyecto efímero
`carrillo-ci-bd4bd35195bf`: **109 passed**, exit 0, cleanup completo. Son casos ya
incluidos en el total; no se suman otra vez. Comando reproducible desde la raíz:

```bash
python3 - <<'PYFOCUSED'
import sys
import tempfile
import uuid
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root))
from scripts.ci import Pipeline

project = 'carrillo-ci-' + uuid.uuid4().hex[:12]
print('Focused MFA project: ' + project, flush=True)
with tempfile.TemporaryDirectory(prefix='mfa-check-', dir=root / '.local') as directory:
    pipeline = Pipeline(root, Path(directory), project)
    try:
        pipeline.validate_isolation()
        pipeline.compose('build', 'backend', 'tests', timeout=1200)
        pipeline.compose('up', '-d', '--wait', '--wait-timeout', '90', 'db')
        pipeline.compose('run', '--rm', 'migrate')
        pipeline.compose('run', '--rm', 'tests', 'python', '-m', 'pytest', '-p', 'no:cacheprovider', 'tests/integration/test_mfa_consumption.py', 'tests/integration/test_mfa_verification.py')
    finally:
        pipeline.cleanup()
print('Focused MFA check passed', flush=True)
PYFOCUSED
```

La salida de esa ejecución se conservó en
`/tmp/carrillo-access-2.24-mfa-focused.log`.

Los casos nuevos prueban el borde de quince minutos ±1 microsegundo; primer fallo
fija inicio y fallos posteriores no desplazan la ventana. Los workers compiten
por la primera inserción y por el último fallo permitido, con bloqueo comprobado
en PostgreSQL. También se ejercita un lock timeout real de tres segundos.
Los fallos antes de commit se inyectan con eventos SQLAlchemy; no simulan una
caída de disco/red durante COMMIT. Fallo de permiso INSERT de auditoría sí ocurre
en DB y revierte todo. El resultado incierto requiere tratamiento del caso de uso.

El login por contraseña y dos pendientes de selección reales dejan intactas las
cuotas MFA. Esto demuestra independencia, **no** login MFA integrado. No hubo
recorrido de navegador nuevo porque no cambia interfaz o contrato HTTP.

## Actualización local

Copia **previa** `.local/development/backups/pre-access-2.24-d72267a3.dump`,
archivo 0600 en carpeta privada, fuera de Git/build. DB saludable e índice verificado;
resultado exit 0 comprobado antes de iniciar la migración. El archivo temporal se
renombra desde `.incomplete` solo al validar. No se ha ensayado restauración
completa: no declararlo backup productivo cifrado o restauración segura.

Comandos ejecutados desde la raíz, sin regenerar credenciales/bootstrap:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.24-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.24-' + uuid.uuid4().hex[:8] + '.dump')
partial = target.with_suffix('.dump.incomplete')
prefix = base + ['exec', '-T', 'db']
with os.fdopen(os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
    subprocess.run(prefix + ['sh', '-c', 'exec pg_dump --username=carrillo_owner --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl'], stdout=output, check=True, timeout=120)
with partial.open('rb') as source:
    subprocess.run(prefix + ['pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=30)
partial.rename(target)
print('Verified PRE-migration private backup: ' + str(target))
PYBACKUP
# Continuar únicamente si el comando de copia anterior terminó con exit 0.
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait backend frontend proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

Primera actualización local: exit 0 en copia previa, migración, arranque,
recarga del proxy, HTTP smoke, revisión y Alembic check. Revisión
`0011_mfa_throttles (head)`, sin operaciones nuevas detectadas. Los cuatro servicios
saludables. La imagen final con el endurecimiento IPv6 también se reconstruyó y arrancó
exit 0, sin otra migración o regeneración de secretos. Se recargó el proxy y HTTP
smoke volvió a pasar (HTML español, 10 assets, mismo origen, cabeceras/404 y rechazo
sin origen/CSRF). Backend, DB, frontend y proxy finales healthy; aplicación en
`http://localhost:8080`.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.24-dev-build-final.log 2>&1
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

## Riesgos y siguiente tarea

Faltan estado/revisión/obligación MFA global, pendientes tipados, pruebas frescas
de contraseña/buzón, todos los caminos de sesión y recuperación, UI/correo y
ensayos de operación. Los contadores actuales no se invocan desde autenticación.
Retención/cardinalidad, cuotas de correo, volumen del proxy y observabilidad de
rechazos siguen pendientes. Un atacante que alcance el flujo autorizado puede
agotar temporalmente los intentos del titular; no se promete eliminar ese riesgo.

Custodia de claves, reloj/restauración, HTTPS y preparación productiva pendientes.
SEC-2026-001 conserva cuatro entradas npm dev-only hasta 2026-10-31 00:00 UTC,
sin ampliarlo o declarar su corrección. Sin certificación ASVS/AAL2.

**Siguiente 2.25:** estado global de seguridad del usuario/revisión monotónica,
base para pendientes tipados y revocación consistente. MFA no se habilita hasta
integrar los caminos completos. Una tarea pequeña por entrega.

Sin compras, correo, acceso VPS, DNS/TLS, despliegue público o push remoto.
