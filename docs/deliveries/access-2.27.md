# Acceso 2.27: verificación TOTP y prueba persistida atómicas

2026-10-17, America/El_Salvador. Servicio interno; login operativo sin MFA.

## Contrato ejecutable

`MfaVerifier.verify_login_totp` acepta token opaco, binding, código SecretStr e IP
validada del proxy confiable. No acepta usuario, empresa, factor, rol, revisión,
fechas o propósito elegidos por el cliente. Descubre identidad sin locks y vuelve
a validar el pendiente login_totp bajo locks después de obtener cuotas IP/cuenta.
Usuario, contraseña/revisión, navegador, propósito y plazo deben seguir vigentes.
No verifica por sí mismo contraseña: el emisor privado exige que su llamador ya
la haya comprobado bajo el mismo lock. HTTP aún no llama a estos servicios.

El factor activo se obtiene del usuario global actual. Código incorrecto conserva
pendiente y confirma fallos compartidos con los demás propósitos. Un token/binding
inválido no consume factor ni drena la cuota de la víctima; hace falta protección
de volumen HTTP/proxy. Cuotas agotadas no evalúan factor. Acierto conserva historial.

Éxito confirma juntos contador global, evento mfa_totp_consumed, cierre del pendiente
y token nuevo login_verified. Solo hashes persistidos; publicación tras commit.
La prueba lleva factor propietario, revisión, huella de contraseña, navegador y
fechas de contraseña/factor. Conserva el límite de cinco minutos desde contraseña,
sin renovación. El emisor tras contraseña rechaza crear login_verified. Rotar esa
etapa conserva evidencia/plazo; consumirla revalida usuario, contraseña, revisión
y factor vigente. No entrega membresías ni sesión por el resultado aislado.

Orden IP -> cuenta -> usuario -> pendiente -> factor; sin locks de empresa después.
Reloj tras esperas; caducar durante factor revierte consumo/evento/cuotas. Fallos
de clave, DB, auditoría, escritura de prueba, commit o reloj fallan cerrados, sin
publicar éxito. Los fallos de infraestructura no se cuentan como códigos erróneos.
No hay callbacks públicos ni reintento automático con commit incierto.

0014 agrega campos/propósito y CHECK/FK de propietario/forma/tiempos. Sin backfill
ni factor activo nuevo; downgrade rechaza prueba incluso consumida. Constraints
no sustituyen autorización ni protegen de SQL privilegiado que falsifique datos
coherentes. No hay sesiones/HTTP/UI, incorporación, correo o recuperación nuevos.

## Archivos completos modificados

- `backend/app/modules/access/mfa_verification.py`: composición y guardia de cuotas compartida.
- `backend/app/modules/access/mfa_pending.py`: etapa verificada, validación y rotación.
- `backend/app/modules/access/models.py`, `backend/migrations/versions/0014_mfa_login_proof.py`: persistencia.
- `backend/tests/integration/test_mfa_login.py`: pruebas PostgreSQL aisladas.
- `backend/tests/integration/test_mfa_pending.py`: propósitos del emisor inicial explícitos.
- `backend/tests/integration/test_mfa_consumption.py`, `test_postgres.py`, `test_user_provisioning.py`: head esperado 0014.
- README, contratos MFA, aceptación, modelo, decisiones y revisión de seguridad.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.27-ci.log 2>&1
```

Los dos primeros CI terminaron con exit 1 al quedar la auditoría Python incompleta
(exit 2), antes de construir/ejecutar la aplicación de pruebas. Informes privados:
`.local/security-reports/cd2c0b8b284b4de0bf0938fb5b3cf6bc.json` y
`.local/security-reports/9eb95b2779a04bd68301a6f0e55356ac.json`.
El diagnóstico sin salida cruda confirmó timeout/ConnectionError/resolución DNS de
pypi.org; informe `.local/security-reports/58682920442d4dd5b2bc805218663aa1.json`.
Posteriormente DNS respondió en host y contenedor aislado. No se modificaron DNS,
timeouts, escáneres, dependencias o excepciones para continuar. Reejecución:

```bash
python3 -m scripts.ci > /tmp/carrillo-access-2.27-ci-after-dns.log 2>&1
```

CI final **exit 0**, proyecto `carrillo-ci-669eacb59309`: **1.181 casos**,
986 backend (26 nuevos), 7 proxy, 152 frontend y 36 scripts. Ruff/formato
(91 archivos), tipos, builds, migraciones roundtrip/check, HTTP smoke y limpieza
pasaron. Auditorías obligatorias passed/accepted, informe privado:
`.local/security-reports/b6d039ce9c42422a95cd57f099dc42f1.json`. Npm conserva las
cuatro entradas de SEC-2026-001; no son cero hallazgos.

Las 26 pruebas nuevas cubren evidencia/rotación/plazo original, uso único,
pending/binding/propósito inválidos sin evaluar factor, presupuesto persistente
compartido y agotado, estado actual, caducidad/regresión de reloj, expiración
durante factor con rollback, errores reales de permisos en auditoría/prueba,
fallo de commit y clave, imposibilidad de emitir prueba desde contraseña sola,
revocaciones, CHECK/downgrade y dos conexiones realmente bloqueadas con un
solo éxito.
Ejecución local: build, copia previa, migración y validaciones exit 0. Backup
privado `.local/development/backups/pre-access-2.27-ca925110.dump`, modo 0600,
índice verificado; no ensayo de restauración completa. Sin cambiar credenciales,
usuarios o factores reales. Head `0014_mfa_login_proof`; check sin operaciones
nuevas. Cuatro servicios saludables. Smoke comprobó HTML español, 10 assets,
mismo origen, 404, cabeceras y rechazo sin Origin/CSRF. Registro de comprobaciones
locales: `/tmp/carrillo-access-2.27-local.log`.
No afirmar preparación productiva, pruebas MFA end-to-end o recuperación física
de un COMMIT por las simulaciones de rollback.

## Ejecución local

Desde la raíz; detenerse si falla el backup. Verificar su índice no equivale a
restaurarlo. No regenerar credenciales o factores para esta entrega interna.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.27-dev-build.log 2>&1
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid

base = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml']
subprocess.run(base + ['up', '-d', '--wait', 'db'], check=True, timeout=90)
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.27-' + uuid.uuid4().hex[:8] + '.dump')
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

MFA aún no se activa. Faltan emisión/resolución de sesiones con prueba persistida,
selección, hooks de roles/factores, cancelación, incorporación/recuperación, correo,
HTTPS, retención y respaldo/rotación/restauración de claves. SEC-2026-001 npm sigue
visible, hasta 2026-10-31 00:00 UTC sin extenderla.
Siguiente 2.28: emisión de sesión desde prueba MFA persistida, dentro del plazo
y con membresía/estado actuales. No publicar el servicio antes de cerrar el flujo.
