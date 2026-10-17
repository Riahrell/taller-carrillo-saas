# Entrega: persistencia y consumo MFA (acceso 2.22)

Fecha: 2026-10-17 (America/El_Salvador).
Rama: `codex/mfa-recovery-contract`.
**Servicios internos; login y confirmación administrativa siguen sin MFA activo.**

## Resultado

Migración **0009_mfa_credentials** y funciones internas de consumo de TOTP/códigos
offline. Solo se persiste ciphertext AEAD y hashes, con propietario global y
restricciones de unicidad/estado. No hay backfill de factores o correos verificados,
claves operativas nuevas, endpoints, QR, frontend o cambio de contraseña/login.

Orden de locks: usuario -> credencial -> conjunto -> código. El estado se relee
con refresh ORM y hora de DB después de esperas. El contador TOTP es global por
factor; cambiar el propósito futuro de verificación no permite repetirlo.
Recuperación marca solo el código esperado sin usar del conjunto vigente. Un
usuario inactivo, factor revocado o identidad/conjunto ajenos no consumen nada.

La app no tiene DELETE en las tres tablas nuevas. Upgrade es aditivo y downgrade
solo funciona sin factores: con cualquier credencial, incluso revocada, rechaza
el borrado para conservar estado y consumos. No borrar filas para forzarlo.

Las funciones no abren sesiones, recuperación o incorporación. Devuelven evidencia
interna sin confirmar; el caso de uso debe verificar contraseña/pendiente/cuota y
confirmar consumo y efectos juntos. No se publican pruebas o códigos antes de
commit. Auditoría global/cuotas y los demás controles siguen pendientes; no
conectar estas funciones directamente a rutas ni crear factores operativos aún.
Contrato y límites completos: [persistencia MFA](../mfa-persistence-contract.md).

## Archivos completos creados o modificados

- `backend/app/modules/access/models.py`: tres modelos, FK compuesta de propietario,
  índices únicos parciales, longitudes y coherencia de estados.
- `backend/migrations/versions/0009_mfa_credentials.py`: esquema, permisos y
  downgrade protegido, sin tocar datos anteriores.
- `backend/app/modules/access/mfa_consumption.py`: consumo bajo locks, sin commit
  propio, con fallo cerrado y errores criptográficos saneados.
- `backend/tests/integration/test_mfa_consumption.py`: 54 casos nuevos en PostgreSQL
  efímero, contención real entre conexiones, rollback/fallo antes de commit,
  revocación concurrente, reloj, estado ORM antiguo, errores AEAD y restricciones.
- `backend/tests/integration/test_postgres.py`,
  `backend/tests/integration/test_user_provisioning.py`: revisión esperada de head
  actualizada a 0009, conservando las pruebas previas de downgrade/evidencia.
- `docs/mfa-persistence-contract.md`, `README.md`, `docs/acceptance.md`,
  `docs/data-model.md`, `docs/decisions.md`, `docs/mfa-recovery-contract.md`,
  `docs/security-review.md`, `docs/reviews/plan-maestro-v1.1.md` y este registro:
  estado, contrato físico, límites y siguiente tarea.

Archivos completos en el repositorio. Sin cambios de dependencias, locks,
configuración, imágenes base, contratos HTTP o frontend.

## Validación ejecutada

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.22-ci-verified.log 2>&1
python3 -m scripts.security > /tmp/carrillo-access-2.22-security-final.log 2>&1
```

Primera ejecución (`/tmp/carrillo-access-2.22-ci.log`): se detuvo por una línea
en blanco sobrante en la migración, antes de pruebas funcionales. Se corrigió
con Ruff y se repitió toda la validación; no se oculta ese fallo como un pase.

Resultados reales de la ejecución verificada:

- CI: **exit 0**, proyecto aislado `carrillo-ci-078a23c77c9f`.
- **1.060 pruebas:** 865 backend (54 nuevas en PostgreSQL), 7 por proxy,
  152 frontend y 36 de scripts. Sin duplicar recuentos de ejecuciones.
- Nuevos casos prueban dos conexiones realmente bloqueadas mediante
  `pg_blocking_pids`, un solo commit ganador y reintento válido si el primero
  revierte. Un fallo posterior a flush revierte consumo y otro cambio juntos.
  El fallo antes de commit se inyecta con un evento de sesión; no simula una
  caída física de red o disco durante COMMIT, cuyo resultado puede ser incierto.
- Refresco ORM evita aceptar un objeto cargado antes de otro consumo confirmado.
  Revocación confirmada mientras el verificador espera lo hace fallar. Hora leída
  tras espera evita aceptar un TOTP que ya salió de ventana.
- Metadata y migración coinciden (`alembic check` en la suite); recorrido vacío
  base/head/downgrade/upgrade y preservación de datos previos pasaron.
  Downgrade con factor revocado fue rechazado sin perder estado.
- Runtime no puede borrar filas MFA; FK/índices/CHECK rechazan propietarios,
  longitudes, estados incompletos y duplicados inválidos.
- Ruff/formato, workflow, tipos, compilaciones, HTTP smoke y limpieza CI pasaron.
- Canary y escaneos de secretos/worktree/historial/Python passed. npm accepted:
  las mismas cuatro entradas SEC-2026-001, sin ampliar la excepción.
  Informe CI `.local/security-reports/e8b24f0c5dfe436e9c10d4639a75267a.json`.
  Escaneo final con documentación incluida: exit 0, mismos estados; informe privado
  `.local/security-reports/b08ea9ee9110419eacc512dd2400820f.json`.
- Whitespace y 27 enlaces relativos de documentación comprobados sin errores.

No se ejecutó recorrido de navegador nuevo: no cambia UI o contrato HTTP. Las
pruebas nuevas no autentican cuentas reales ni activan MFA de desarrollo.

## Actualización local

Se construyó la imagen backend y se creó una copia privada antes de migrar:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.22-dev-build.log 2>&1
python3 - <<'PY'
import os
from pathlib import Path
import subprocess
import uuid
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.22-' + uuid.uuid4().hex[:8] + '.dump')
prefix = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml', 'exec', '-T', 'db']
with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
    subprocess.run(prefix + ['sh', '-c', 'exec pg_dump --username=carrillo_owner --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl'], stdout=output, check=True, timeout=120)
with target.open('rb') as source:
    subprocess.run(prefix + ['pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=30)
print('Private local backup created; archive listing verified: ' + str(target))
PY
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

Copia `.local/development/backups/pre-access-2.22-2f835ee9.dump`, archivo 0600,
fuera de Git/build. Índice verificado; **no se ha ensayado restauración completa**.
Contiene información privada/hashes y no debe publicarse o adjuntarse al chat.

Resultados locales: build, backup, migración, recreación/recarga y HTTP smoke
exit 0; revisión `0009_mfa_credentials (head)` y `alembic check` sin operaciones
nuevas. Backend, DB, frontend y proxy saludables. Aplicación disponible en
`http://localhost:8080`, sin crear factores reales ni cambiar credenciales.

## Riesgos y siguiente tarea

MFA/recuperación no están activos. Faltan auditoría global append-only, cuotas
compartidas, pendientes, revisión/obligación admin y comprobación de todos los
caminos de sesión y reautenticación, incorporación con buzón y recuperación segura.
El hecho de consumir un código atómicamente no demuestra esos controles.
La posición limita a diez códigos por conjunto; la futura activación debe crear
los diez en un commit y gestionar entrega una vez después de confirmar.

Custodia/rotación de claves, correo, reloj/restauración, HTTPS y preparación
productiva siguen pendientes. TOTP no resiste phishing; passkeys siguen previstas.
SEC-2026-001 vence 2026-10-31 00:00 UTC, sin renovación o corrección declarada.
Sin defensa afirmada frente a SQL arbitrario privilegiado ni certificación ASVS.

**Siguiente 2.23:** auditoría global append-only para consumo MFA, atómica con
consumo y con pruebas de privilegios/rollback. Después cuotas y restantes controles
antes de conectar autenticación/recuperación al navegador.

Sin compras, correo, cambios en VPS/DNS/TLS, despliegue público o push remoto.
