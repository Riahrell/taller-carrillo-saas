# Entrega: auditoría global de consumo MFA (acceso 2.23)

Fecha: 2026-10-17 (America/El_Salvador).
Rama: `codex/mfa-recovery-contract`.
**Auditoría interna; MFA sigue sin estar activo en login o recuperación.**

## Resultado

0010 añade `security_events` con UUID de evento, usuario sujeto, credencial,
acción y hora de verificación. Solo admite `mfa_totp_consumed` y
`mfa_recovery_consumed`, ligados al propietario del factor por FK compuesta.
No declara login completado, identidad administrativa o recuperación autorizada.
No contiene tenant/actor comercial ficticio, códigos, semillas, hashes offline,
ciphertext, email/IP o payload libre. No inventa eventos históricos.

Cada consumidor actualiza su estado e inserta evidencia en el mismo flush y
transacción. Si falla INSERT del evento, rollback descarta también el consumo;
fallos posteriores o antes del commit descartan ambos. Los lectores externos
no ven ninguno antes del commit. Replay/denegación no producen eventos de éxito.
Las verificaciones concurrentes existentes conservan un único consumo/evento.

La app tiene SELECT/INSERT, sin UPDATE/DELETE/TRUNCATE o propiedad de la tabla.
La evidencia se conserva frente a ese rol, no frente al propietario de migraciones.
No se afirma WORM, firmas, retención externa o defensa ante SQL arbitrario capaz
de insertar eventos falsos. El contrato de aplicación mantiene tipos/campos del
servidor. No hay rutas de consulta/exportación global para admins de empresa.

Downgrade se rechaza ante eventos **o factores**, aunque no tengan consumos, para
no volver a consumidores sin auditoría. El recorrido de esquema vacío se mantiene.
No eliminar filas para forzar una reversión. Sin backfill, cambios de cuentas,
claves operativas, dependencias, contratos HTTP o frontend.

## Archivos completos creados o modificados

- `backend/app/modules/access/models.py`: modelo SecurityEvent, acciones limitadas,
  FK de propietario y un índice de usuario/hora para futura consulta autorizada.
- `backend/migrations/versions/0010_mfa_security_events.py`: tabla, permisos y
  downgrade protegido, sin eventos históricos o factores nuevos.
- `backend/app/modules/access/mfa_consumption.py`: evento de tipo/hora del servidor
  en la misma transacción que cada consumo aceptado.
- `backend/tests/integration/test_mfa_consumption.py`: 21 casos nuevos y aserciones
  añadidas a pruebas existentes de contención/rollback; limpieza exclusiva del
  propietario en PostgreSQL efímero, nunca cuentas operativas.
- `backend/tests/integration/test_postgres.py`,
  `backend/tests/integration/test_user_provisioning.py`: revisión esperada de head
  0010, conservando verificaciones de migraciones/evidencia anteriores.
- `README.md`, `docs/acceptance.md`, `docs/data-model.md`, `docs/decisions.md`,
  `docs/mfa-persistence-contract.md`, `docs/mfa-recovery-contract.md`,
  `docs/reviews/plan-maestro-v1.1.md`, `docs/security-review.md` y este registro:
  estado, alcance, límites y siguiente tarea.

Archivos completos disponibles en el repositorio. No hay implementación extensa
nueva de autenticación o cambios en módulos comerciales.

## Comandos de validación

Desde la raíz del repositorio:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.23-ci.log 2>&1
python3 -m scripts.security > /tmp/carrillo-access-2.23-security-final.log 2>&1
```

Resultados reales:

- CI **exit 0**, proyecto aislado `carrillo-ci-8c1983c0fa73`.
- **1.081 pruebas:** 886 backend, 7 por proxy, 152 frontend y 36 scripts.
  La suite de consumo/auditoría tiene 75 casos, incluidos 21 nuevos en esta entrega.
- Ruff/formato (80 archivos Python), workflow, tipos, builds, migraciones,
  HTTP smoke y limpieza del proyecto CI pasaron.
- Canary, secretos de worktree/historial y locks Python passed; npm accepted
  con las mismas cuatro entradas SEC-2026-001. No se declara cero avisos.
  Informe CI `.local/security-reports/3d3d28bfa291426b95aa5b5dfb267296.json`.
  Escaneo final incluyendo documentación: exit 0, mismos estados; informe privado
  `.local/security-reports/260d67c6fe3f4ea59dbce2ffb3f7c31c.json`.
- Whitespace y 27 enlaces relativos de documentación comprobados sin errores.
- Recorrido vacío base/head/base/head y Alembic check pasaron con el nuevo esquema.
  Datos/privilegios de entregas anteriores siguen cubiertos por sus pruebas.

Los casos nuevos verifican evento/tipo/hora/propietario, visibilidad al commit,
fallo real de INSERT por permisos con rollback del código, denegación con SQLSTATE
42501 al intentar UPDATE/DELETE/TRUNCATE/DROP desde runtime, acciones/identidad/fecha
inválidas y downgrade protegido. El fallo previo al commit se inyecta con un evento
de sesión; no equivale a simular caída de disco/red durante COMMIT. Un resultado
incierto de commit exige tratamiento del caso de uso, sin reintentos ciegos.

Las pruebas de contención usan dos conexiones y comprueban bloqueo real en
PostgreSQL; ahora también exigen exactamente un evento confirmado. Sin recorrido
nuevo de navegador porque no cambia UI/HTTP. No se autentican cuentas reales.

## Actualización local

El entorno local estaba detenido. El primer intento de copia previa falló con
`service "db" is not running`; el archivo `pre-access-2.23-7e749357.dump` quedó
vacío. Se inició la migración antes de detener la secuencia por ese fallo: error
de orden de ejecución, no una copia previa válida. Compose arrancó DB y 0010
terminó exit 0. Es una migración aditiva sin modificaciones de cuentas/sesiones.
No se ha revertido el esquema para ocultar ese orden.

Se identificó el archivo vacío como `.dump.failed` y se creó una copia **posterior**,
`.local/development/backups/post-access-2.23-e4359db9.dump`, 0600 dentro de carpeta
privada 0700, fuera de Git/build. Índice verificado; no se ensayó restauración
completa ni se declara copia previa o backup productivo cifrado. No publicar o
adjuntar estos archivos con datos/hashes privados.

Comandos exitosos utilizados desde la raíz, sin regenerar credenciales/bootstrap:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.23-dev-build.log 2>&1
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import subprocess
import uuid
failed = Path('.local/development/backups/pre-access-2.23-7e749357.dump')
if failed.exists() and failed.stat().st_size == 0:
    failed.rename(failed.with_suffix('.dump.failed'))
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('post-access-2.23-' + uuid.uuid4().hex[:8] + '.dump')
prefix = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml', 'exec', '-T', 'db']
with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
    subprocess.run(prefix + ['sh', '-c', 'exec pg_dump --username=carrillo_owner --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl'], stdout=output, check=True, timeout=120)
with target.open('rb') as source:
    subprocess.run(prefix + ['pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=30)
print('Private POST-migration backup created; archive listing verified: ' + str(target))
PYBACKUP
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait backend frontend proxy
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

Comprobaciones locales finales: exit 0 en build, migración, copia posterior,
arranque de servicios, HTTP smoke, revisión y Alembic check. Revisión
`0010_mfa_security_events (head)`, sin operaciones nuevas detectadas. Backend, DB,
frontend y proxy saludables; aplicación en `http://localhost:8080`. HTTP smoke
verificó HTML español, 10 assets, mismo origen, cabeceras/404 y rechazo de escritura
sin origen/CSRF válidos. Sin recorrido manual de cuenta real o MFA operativo.

## Riesgos y siguiente tarea

No hay MFA operativo todavía. Faltan cuotas MFA compartidas, pendientes,
contraseña/buzón verificados, revisión/obligación global y todos los caminos de
sesión/selección/reautenticación/recuperación. Solo se registran consumos aceptados;
rechazos, incorporación/sustitución y otros eventos se integrarán con sus servicios.
No se ofrece lectura global por API, alertas, retención o exportación externa.

Custodia/rotación de claves, backups con restauración ensayada, reloj, HTTPS y
preparación productiva siguen pendientes. El aviso npm SEC-2026-001 continúa con
cuatro entradas aceptadas hasta 2026-10-31 00:00 UTC; sin ampliarlo o declararlo
corregido. Sin certificación ASVS/AAL2 ni seguridad absoluta.

**Siguiente 2.24:** cuotas MFA independientes de contraseña y compartidas entre
propósitos, con ventanas, fallos persistentes y contención probados antes de
activar autenticación MFA. Se conserva el límite de una tarea pequeña por entrega.

Sin compras, correos, acceso a VPS, DNS/TLS, despliegue público o push remoto.
