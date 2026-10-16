# Entrega: PostgreSQL, SQLAlchemy y Alembic (fundación 1.2)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/foundation-backend`. El hito 1 completo sigue pendiente.

## Resultado y decisiones técnicas

- PostgreSQL 18.6 privado en Compose; imagen fijada por digest.
- SQLAlchemy 2.0.54 síncrono, psycopg 3.3.6 y Alembic 1.20.0, con hashes.
- URL obligatoria como SecretStr o archivo secreto, sin credencial de reserva.
  Rechazar fuentes simultáneas, URLs incompletas, otros drivers y query params.
- Roles separados: bootstrap solo en DB; owner para migraciones; app para DML.
  Owner y app no son superusuarios ni pueden crear bases/roles. App sin DDL en public.
- API comprueba conexión al arrancar; engine compartido por proceso, sesiones por
  caso de uso y cierre explícito. Conexiones UTC, pre-ping y parámetros SQL ocultos.
- Migración inicial vacía de negocio: solo cadena y `alembic_version`.
- Migraciones como comando separado del arranque. No usar `metadata.create_all`.
- Contenedor Python UID 10001, filesystem de solo lectura y capacidades retiradas.
- Desarrollo con volumen persistente; pruebas con otro proyecto/secretos y tmpfs.
- DB solo en red interna. API con red interna y red de entrada separada; solo
  override dev publica loopback:8000. Ningún puerto PostgreSQL publicado al host.

## Archivos creados o modificados

- Raíz: `.gitignore`, `.env.example`, `README.md`, `compose.yaml`,
  `compose.dev.yaml`, `compose.test.yaml`.
- `backend/pyproject.toml`, locks runtime/dev, `Dockerfile`, `.dockerignore`.
- `backend/app/core/config.py`, `database.py` y `backend/app/main.py`.
- `backend/alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`,
  `migrations/versions/0001_foundation.py`.
- `backend/tests/conftest.py`, `test_app.py`, `test_config.py`,
  `integration/test_postgres.py`.
- `ops/postgres/init.sh`, `scripts/setup_local.py`.
- `docs/decisions.md`, `docs/acceptance.md` y este registro.
- Credenciales locales generadas en `.local/`: ignoradas por Git, sin valores
  en documentación. El generador rechaza sobrescribir entornos existentes.

## Comandos reproducibles ejecutados

Desde raíz, primera creación de entornos:

```bash
python3 scripts/setup_local.py development
python3 scripts/setup_local.py test
```

No repetir el generador sobre un entorno existente. Desarrollo:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait db
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait backend
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health/live
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
docker inspect carrillo-dev-db-1 --format '{{json .HostConfig.PortBindings}}'
docker inspect carrillo-dev-backend-1 --format '{{.Config.User}} {{.HostConfig.ReadonlyRootfs}}'
```

Batería completa y repetición desde una base vacía:

```bash
docker compose --project-name carrillo-test --env-file .local/test/compose.env -f compose.yaml -f compose.test.yaml run --build --rm tests
docker compose --project-name carrillo-test --env-file .local/test/compose.env -f compose.yaml -f compose.test.yaml down
docker compose --project-name carrillo-test --env-file .local/test/compose.env -f compose.yaml -f compose.test.yaml run --rm tests
```

Validaciones locales:

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pip check
sh -n ops/postgres/init.sh
git diff --check
```

La instalación/sincronización utilizó los locks con `--require-hashes`. Se
comprobaron también mediante scripts de validación: permisos de `.local`, rechazo
de sobrescritura, coherencia de locks y configuración JSON resuelta de Compose
(montaje del inicializador, secretos por servicio, red interna y tmpfs de test).

## Resultados reales

- **36 pruebas aprobadas dentro de Docker**, incluidas cinco de PostgreSQL real:
  upgrade/check/downgrade/upgrade; roles/UTC; rollback transaccional; arranque API;
  credencial inválida con excepción sin exponer URL/password.
- **31 pruebas locales aprobadas**, cinco de integración excluidas expresamente.
- Ruff check y formato correctos para los 12 archivos Python de esta tarea/base.
- `pip check`: sin dependencias rotas. `sh -n`: sin errores.
- API y DB saludables; HTTP real: `{"status":"ok"}`.
- Alembic current: **0001_foundation (head)**.
- PortBindings DB: **{}**. API: **127.0.0.1:8000**.
- Usuario/fs API: **10001 true**.
- El generador no sobrescribió secretos y conservó directorios 0700/env 0600.
- Configuraciones dev/test resueltas comprobadas sin mostrar valores secretos.
- Repetición final desde DB vacía: **36 passed in 0.49s**.
- La revisión aplicada y HTTP se conservaron tras recrear el contenedor DB dev.

## Incidencias corregidas

- El override test retiraba también el bind del inicializador al reiniciar
  volúmenes; los roles no se crearon. El inicializador ahora es un `config`
  independiente del almacenamiento. Se repitió desde un contenedor vacío.
- Un traceback de psycopg mostró una credencial desechable de test. Se retiró
  ese contenedor, se sustituyeron los secretos test y se agregaron conexiones
  con errores saneados, tracebacks cortos y una regresión con fallo de auth real.
  Ningún secreto de desarrollo se mostró ni se reutiliza entre entornos.
- Una API conectada solo a red interna no publicó su puerto con este Docker.
  Se separó red de entrada de red DB; HTTP y bindings finales se verificaron.
- Health check DB por TCP loopback para esperar al servidor definitivo, sin
  confundirlo con el socket del servidor temporal que ejecuta la inicialización.
- Docker, instalaciones y pruebas ASGI requirieron permisos fuera del sandbox.

## Límites y siguiente tarea

- No hay login, tenant, tablas comerciales, frontend, proxy HTTPS ni CI todavía.
  No incorporar rutas de negocio antes de protección por defecto e aislamiento.
- No hay backup/restauración ni política de rotación operacional implementada.
- TLS hacia bases remotas no está habilitado; el entorno actual es local/privado.
- Liveness no equivale a readiness continuo ni comprobación de esquema.
- La política fiscal sigue pendiente antes de cobro real y producción.
- Entrega registrada en commit local en la rama de fundación, sin push remoto.
- Desarrollo queda disponible localmente; los recursos efímeros de test se retiran.

Siguiente: fundación 1.3, Next.js/TypeScript mínimos y proxy de mismo origen;
después CI para cerrar el hito 1.
