# Entrega: servicio de alta administrativa (acceso 2.13)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-user-provisioning`.

## Resultado y alcance

`MembershipAdministrator.create_user` crea una cuenta global nueva, su membresía
en la empresa del administrador y el evento `membership_created` en un commit.
Es un servicio interno con contrato probado; todavía sin endpoint ni formulario
de alta. La pantalla de membresías existentes y el login siguen funcionando.
No se crearon usuarios operativos ni se cambiaron contraseñas para esta entrega.

La entrada `UserCreation` admite solo email/password/role. Reutiliza validación
de login para correo/Unicode, exige contraseña de 15–128 caracteres sin recorte y
rol objetivo explícito entre los cuatro existentes. Contraseña SecretStr excluida
de repr/serialización del comando, hash Argon2id existente solo tras autorizar.
Cuenta y membresía activas decididas por servidor; sin estado activo del cliente,
tenant_id, user_id, hash, sesión automática o contraseña predeterminada.

La sesión resuelve empresa, usuario, membresía y permiso admin actuales bajo
locks. expected_actor obligatorio compara el contexto esperado con el actor real,
sin conceder autoridad ni elegir identidad/empresa. El nuevo usuario nunca puede
ser la identidad de un correo global ya registrado: mismo conflicto para cuenta
propia, de otra empresa o inactiva, sin adjuntar, reactivar, reemplazar contraseña
ni divulgar IDs/tenant/estado. El conflicto sí comunica indisponibilidad de ese
correo al administrador autorizado; no se promete evitar esa inferencia.

La unicidad global de correo y ON CONFLICT DO NOTHING arbitran altas concurrentes,
incluyendo dos administradores en empresas distintas. Solo uno confirma; el otro
no deja membresía ni auditoría parcial. El lock de empresa comparte la coordinación
de último admin. El servicio no modifica accesos existentes ni revoca al actor.
Fallo de hash/inserción/auditoría/commit revierte la operación y devuelve error
saneado. Sin retries ni idempotencia general; una respuesta perdida requiere
revisar el listado antes de otra alta.

Migración **0007_membership_creation_audit**: permite antes null exclusivamente
para el evento de alta, conserva reglas estrictas de cambio y permisos SELECT/
INSERT de aplicación. Conserva los eventos anteriores. Un downgrade a 0006
con eventos de alta aborta antes de modificar esquema: no se retira evidencia ni
se fabrica un estado previo. Sin cambios de dependencias o infraestructura.

También se corrigió el mensaje final de bootstrap: ahora confirma que se puede
iniciar sesión en la web, en lugar del texto obsoleto que decía que estaba pendiente.
No cambió la entrada oculta, la contraseña ni el comportamiento de creación inicial.

## Archivos completos creados o modificados

- `backend/app/modules/access/administration.py`: contrato UserCreation, conflicto
  genérico y servicio transaccional sobre autorización/locks existentes.
- `backend/app/modules/access/models.py`: audit_events admite evento tipado de
  alta; estado previo nullable con CHECKs que distinguen alta/cambio.
- `backend/migrations/versions/0007_membership_creation_audit.py`: ampliación
  compatible de auditoría y reversión protegida frente a pérdida de evidencia.
- `backend/tests/test_user_creation.py`: 21 casos nuevos de contrato, perfiles,
  límites Unicode, campos extra y secreto fuera de repr/serialización.
- `backend/tests/integration/test_user_provisioning.py`: 35 casos nuevos con
  PostgreSQL restringido; autorización, identidad/empresa, duplicados, login real
  del nuevo usuario, rollback, locks reales, último admin, CHECKs y migración.
- `backend/tests/integration/test_postgres.py`: revisión esperada 0007 en el
  recorrido upgrade/check/downgrade/upgrade existente.
- `backend/app/modules/access/bootstrap.py` y `backend/tests/test_bootstrap.py`:
  mensaje final vigente y comprobación de ese mensaje en el test existente.
- `docs/user-provisioning-contract.md`: contrato, autoridad, transacción,
  concurrencia, auditoría, límites y aceptación de este servicio interno.
- `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/data-model.md`, `docs/decisions.md`, `docs/permissions.md` y este registro:
  alcance actual, esquema, resultados y siguiente tarea.

## Comandos exactos de validación ejecutados

Desde la raíz:

```bash
.venv/bin/python -m ruff check --fix --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_user_creation.py
python3 -m scripts.ci > /tmp/carrillo-access-2.13-ci.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.13-final-ci.log 2>&1
git diff --check
```

Para repetir el conjunto completo basta `python3 -m scripts.ci`. Sus credenciales
y proyecto aleatorio son temporales, DB carrillo_test usa tmpfs y guards. Todas
las pruebas destructivas y reversiones de esquema suceden exclusivamente allí.
El runner limpia sus contenedores, redes, imágenes propias y archivos temporales.

Copia privada previa a migrar desarrollo, realmente ejecutada:

```bash
python3 - <<'PY'
import os
from pathlib import Path
import subprocess
import uuid
folder = Path('.local/development/backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('pre-access-2.13-' + uuid.uuid4().hex[:8] + '.dump')
prefix = ['docker', 'compose', '--project-name', 'carrillo-dev', '--env-file', '.local/development/compose.env', '-f', 'compose.yaml', '-f', 'compose.dev.yaml', 'exec', '-T', 'db']
with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
    subprocess.run(prefix + ['sh', '-c', 'exec pg_dump --username=carrillo_owner --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl'], stdout=output, check=True, timeout=120)
with target.open('rb') as source:
    subprocess.run(prefix + ['pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=30)
print('Private local backup created; archive listing verified: ' + str(target))
PY
```

Archivo generado: `.local/development/backups/pre-access-2.13-a010f4dd.dump`, fuera
de Git y del build. Directorio 0700, archivo 0600. Se comprobó el índice del archivo
con pg_restore --list; **no se ejecutó una restauración completa de esa copia**.
Contiene datos privados de la base, incluidos hashes; no publicarlo ni adjuntarlo
a conversaciones. Este respaldo local no sustituye el plan de backups/restore
de producción, todavía pendiente.

Actualización local:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

## Resultados reales

- Unitarias nuevas: **21 passed en 0.16s**.
- Primera ejecución CI **carrillo-ci-0d26d739393c**: **535 passed, 1 failed**.
  El test de pérdida de permiso capturó el PID de la conexión que retenía el lock
  porque el listener ya estaba activo durante su preparación. Se acotó el listener
  al intento concurrente para observar la conexión bloqueada. Ese primer intento
  no se cuenta como aprobado. Ruff también indicó formato/líneas largas antes
  de formatear y dividir los SQL literales; quedaron corregidos antes del CI final.
- Pipeline final **carrillo-ci-5f928101992c**, exit 0, **CI passed**.
- **536 backend** en 33.79s: **214 unitarias + 322 PostgreSQL**. Más **5 casos
  por proxy real** en 0.67s, sin contarlos dos veces.
- **90 frontend** en 1.16s y **13 scripts/configuración**: **644 pruebas** en
  total, **56 nuevas** respecto a 2.12 (21 unitarias y 35 PostgreSQL).
- Ruff/formato de **64 archivos Python**, pip check, actionlint, ESLint,
  Prettier y tipos aprobados. Imágenes construidas; frontend sin cambios reutilizó
  el build Next.js ya validado, sin afirmar una nueva compilación sin caché.
- Los cuatro roles objetivo se crean con hash real, auditoría y tenant correcto;
  luego autentican con el verificador real. Creación no emite sesión. Cuenta de
  otra empresa, incluso inactiva, no se adjunta ni se modifica por conocer correo.
- Dos altas simultáneas, tanto en la misma empresa como en empresas distintas,
  observaron espera real con pg_blocking_pids y produjeron exactamente un usuario,
  una membresía y un evento. El perdedor recibió conflicto sin efectos parciales.
- Permiso revalidado después de esperar lock: el actor degradado no puede dar
  de alta. Sesiones ausentes/inválidas/revocadas/caducadas/inactivas y contexto
  distinto rechazan antes de hashear. No-admin tampoco puede sondear disponibilidad
  antes de autorizar. El nuevo segundo admin cuenta para retener el último.
- Fallos inyectados de hash, inserción de membresía, auditoría y commit revierten
  todos los registros de alta; error saneado sin canario. CHECKs de auditoría
  rechazan acción/estado previos/finales inválidos. Eventos de cambio sobreviven
  downgrade/upgrade; eventos de alta impiden downgrade sin pérdida ni cambio de
  revisión. Privilegios UPDATE/DELETE de auditoría siguen retirados de la app.
- Sin endpoints nuevos, cambios visuales, cuentas reales creadas, publicación ni
  push. No se ejecutó recorrido de navegador en esta entrega de servicio interno.
- Desarrollo: backup privado previo, migración aplicada y revisión observada
  **0007_membership_creation_audit (head)**. Alembic check: **No new upgrade
  operations detected**. Backend reconstruido/recreado, Nginx recargado y **cuatro
  servicios saludables**. Smoke HTTP exit 0: HTML español, diez assets, API mismo
  origen, 404 y rechazos de escritura sin origen/CSRF. No se ejecutó downgrade
  ni se restauró la copia sobre la base operativa local.

## Riesgos pendientes y siguiente tarea

El alta aún no se puede usar desde la web. El próximo adaptador debe aplicar
sesión/admin, CSRF/origen, contexto, validación/límites y errores sin eco. Debe
concretar elección/entrega de contraseña mediante un canal controlado; este
servicio no proporciona correo verificado, invitación, cambio inicial obligatorio
ni recuperación. Nunca volver a asignar una cuenta global por conocer su correo.

Sin idempotencia general ni retry: confirmar el estado si se pierde una respuesta.
Hashing mantiene locks brevemente; medir capacidad antes de ampliar carga.
Sin RLS ni defensa contra SQL arbitrario privilegiado. Reversión de migración
condicionada a no tener eventos de alta; con evidencia real, preferir corrección
hacia delante y conservarla. No borrar auditoría para forzar un downgrade.

Hito 2 abierto: HTTP/UI de altas, cuentas/contraseñas globales, permisos de otros
módulos, auditoría consultable, recuperación y limpieza. TLS público, primera
CI remota, backups con restauración ensayada y capacidad siguen pendientes.
No se afirma preparación para producción ni modo offline.

Siguiente tarea **2.14**: adaptador HTTP de alta administrativa sobre este servicio,
con pruebas de sesión/admin, CSRF, contexto, datos extra, duplicados y commit,
y definición del canal de contraseña antes de exponer el formulario.
