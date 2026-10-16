# Entrega: administración interna de membresías (acceso 2.9)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-membership-administration`.

## Resultado

Servicio interno para cambiar el rol/estado de una membresía existente. Obtiene
identidad y empresa del token privado de sesión y vuelve a verificar vigencia,
estados y permiso admin en la misma transacción. No admite tenant, actor ni
contexto externo como autoridad. El rol solicitado describe el cambio; solo un
administrador autorizado puede aplicarlo. Contrato explícito, enum de roles,
booleano estricto, campos extra prohibidos. Sin rutas HTTP nuevas ni pantalla.

Desactivar o degradar al último administrador utilizable se rechaza, también con
dos solicitudes simultáneas. Un administrador cuenta solo si usuario y membresía
están activos. Se permite retirarse el propio acceso cuando queda otro admin.
El servicio no modifica usuarios globales ni permite reasignar IDs o empresa.

Un cambio real revoca todas las sesiones de la membresía y cancela los pendientes
globales del usuario afectado, preservando sesiones de otras empresas. Registra
autor/objetivo, empresa, roles/estados antes/después y fecha de DB. Todo confirma
junto; errores de persistencia o commit revierten cambio, revocaciones y evento.
Reactivar no restaura tokens. Un estado idéntico no revoca ni audita, aunque la
lectura autenticada actualiza actividad del actor. No hay retry automático.

Se prebloquea empresa, luego todos los usuarios implicados por UUID antes de
acceder a hijos. El lock de empresa serializa el conteo de admins. Login, sesión
y logout siguen sus protocolos de usuario sin adquirir luego locks de empresa.
Detalle y obligaciones de operaciones futuras en decisions.md.

`0006_membership_audit` añade `audit_events` tipada para membership_changed,
con FK compuestas de actor/objetivo a la misma empresa, CHECK y RESTRICT.
El runtime puede SELECT/INSERT, pero no UPDATE/DELETE; el propietario DB conserva
autoridad. Sin payload arbitrario, credenciales, correo, IP ni nuevos campos
comerciales. No es una auditoría completa de toda la aplicación.

## Archivos completos creados o modificados

- `backend/app/modules/access/administration.py`: comando, resultado, errores
  seguros y servicio dueño de autorización/transacción/bloqueos/reglas/revocación.
- `backend/app/modules/access/models.py`: AuditEvent tipado y restricciones.
- `backend/migrations/versions/0006_membership_audit.py`: tabla, índice, FK,
  restricciones y reducción de privilegios de carrillo_app.
- `backend/tests/test_membership_change.py`: 14 casos de validación del comando.
- `backend/tests/integration/test_membership_administration.py`: 36 casos reales
  PostgreSQL: autorización, aislamiento, estados, no-op, reactivación, revocaciones,
  auditoría, rollback, último admin y concurrencia observada mediante pg_locks.
- `backend/tests/integration/test_postgres.py`: nueva revisión esperada; conserva
  upgrade/check/downgrade/upgrade contra la base efímera.
- `README.md`, `docs/permissions.md`, `docs/decisions.md`, `docs/data-model.md`,
  `docs/acceptance.md`, `docs/auth-http-contract.md` y este registro: estado,
  contratos, límites y siguiente tarea.

Sin cambios de dependencias, frontend, configuración de secretos o Compose.

## Comandos exactos ejecutados

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts --fix
.venv/bin/python -m ruff format --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_membership_change.py
python3 -m scripts.ci
python3 -m scripts.ci > /tmp/carrillo-access-2.9-ci.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.9-final-ci.log 2>&1
git diff --check
```

Para repetir el conjunto completo basta `python3 -m scripts.ci`. Los tres runs de
arriba registran la secuencia efectivamente ejecutada, no tres suites distintas.
El runner crea proyecto/credenciales temporales propios, APP_ENV=test y DB tmpfs
carrillo_test, comprueba aislamiento y elimina sus contenedores/redes/imágenes y
directorio temporal al finalizar, también si hay fallos. Ninguna prueba de
membresías se ejecutó contra el volumen de desarrollo.

Actualización del entorno de desarrollo ya existente:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

La migración es aditiva: crea auditoría y modifica los permisos de esa tabla;
no cambia cuentas/membresías ni genera eventos sintéticos en desarrollo. No se
crearon usuarios reales ni se regeneraron secretos. No se ejecutó downgrade en
desarrollo; destruiría evidencia. La reversibilidad se comprobó solo en DB efímera.

## Resultados reales

- Final **carrillo-ci-1e80b71cee5b**, exit 0, **CI passed**.
- **357 backend** en 17.66s: 166 unitarias y 191 de integración PostgreSQL.
  Más **4 por proxy real** en 0.55s: **361 backend** en total.
- **51 frontend** y **13 scripts/configuración**: **425 pruebas** en el pipeline,
  **50 nuevas** respecto a acceso 2.8. No sumar ejecuciones repetidas.
- Ruff y formato correctos en 57 archivos Python; pip check, actionlint, ESLint,
  Prettier y tipos correctos. Imágenes construidas con dependencias fijadas.
  El build Next.js usó la capa ya validada de acceso 2.8: no se afirma compilación
  frontend nueva sin caché ni cambios de interfaz en esta tarea.
- Migraciones upgrade/check/downgrade/upgrade correctas y metadata sin diferencia.
  Prueba con carrillo_app de denegación UPDATE/DELETE de auditoría; lectura/inserción
  válidas. FK compuestas rechazan actor/objetivo ajenos incluso mediante SQL.
- Concurrencia con conexiones independientes y espera observada en pg_locks:
  dos auto-degradaciones dejan un admin; sesión/reto nuevos antes del cambio quedan
  revocados; emisión/selección tras desactivación no crean sesión; permiso se
  relee tras esperar; actores cruzados entre empresas completan sin invertir locks.
- Fallos inyectados en INSERT de auditoría y antes de commit: ninguna modificación,
  revocación ni evidencia parcial; error saneado sin detalles de infraestructura.
- Primer pipeline **267dd80be007**: 352 passed, 3 failed, exit 1. Los tres fallos
  eran del arnés concurrente: añadir/quitar listeners de SQLAlchemy mientras otra
  conexión iteraba esos listeners causaba deque mutated during iteration. Se
  registran ahora antes de arrancar workers y se retiran después de finalizarlos.
  El formato inicial también se corrigió; no se desactivaron reglas ni tests.
- Segundo **82070fd46071**: 355 backend + 4 proxy y resto aprobados, exit 0.
  Luego se añadieron dos comprobaciones por riesgos abiertos (permiso tras espera
  y actores entre empresas); el pipeline final aprobó los 357 casos actuales.
- Desarrollo: migración confirmó **0006_membership_audit (head)**, backend
  recreado saludable, Nginx recargado y **cuatro servicios saludables**. Solo proxy
  publica loopback:8080. Smoke aprobado: HTML español, diez assets, API mismo
  origen, 404 y protecciones de escritura/cabeceras.
- Primer smoke desde sandbox no pudo abrir sockets (Operation not permitted).
  Se repitió con autorización de red y aprobó, exit 0. No fue un fallo del servidor.
- No se hizo prueba visual nueva ni se afirma un recorrido E2E de administración:
  todavía no existe ese contrato HTTP ni pantalla. El formulario de 2.8 sigue
  servido en http://localhost:8080 y su suite se ejecutó nuevamente en CI.

## Riesgos y siguiente tarea

Hito 2 abierto. Este servicio interno no puede operar aún desde el navegador.
Faltan altas/invitaciones, operaciones globales de usuario/contraseña, permisos por
módulo, consulta y ampliación de auditoría, recuperación y limpieza de registros.
La garantía de último admin depende de usar servicios coordinados; SQL arbitrario
de runtime/owner y futuras operaciones sin el protocolo no quedan protegidos.
Las altas/cambios globales deberán bloquear empresas afectadas por UUID antes de
usuarios. No hay RLS ni auditoría a prueba del administrador DB.

Timeout/lock/commit fallidos no se reintentan automáticamente. Una pérdida de
respuesta puede dejar resultado incierto; el adaptador futuro deberá contemplarlo,
especialmente si el actor se retiró su propio acceso. No se promete idempotencia
comercial, capacidad de carga ni protección de módulos que aún no existen.

Siguiente tarea **2.10**: cerrar el contrato HTTP de administración de membresías
existentes y conectar este servicio con cookies/CSRF, traducción segura de errores
y pruebas de permisos/aislamiento mediante peticiones directas. La UI vendrá
después. Producción, HTTPS, backups/restauración y CI remota siguen pendientes.

Referencias técnicas consultadas: [bloqueos PostgreSQL](https://www.postgresql.org/docs/current/explicit-locking.html),
[transacciones SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block),
[REVOKE PostgreSQL](https://www.postgresql.org/docs/current/sql-revoke.html).
