# Taller Carrillo

Sistema web de gestión integral en construcción incremental. Monolito modular
FastAPI y Next.js, PostgreSQL y Docker Compose.

## Estado: fundación 1.2

API mínima, configuración Pydantic v2, SQLAlchemy 2.x síncrono, PostgreSQL privado,
Alembic, Dockerfile y entorno de pruebas aislado. Sin autenticación, frontend ni
operaciones comerciales. El hito 1 sigue en progreso. No usar en producción.

- [Decisiones](docs/decisions.md), [modelo](docs/data-model.md) y [permisos](docs/permissions.md).
- [Aceptación](docs/acceptance.md) y [entrega 1.2](docs/deliveries/foundation-1.2.md).
- La entrega 1.1 es un registro histórico; el arranque actual requiere DB.

## Requisitos

Docker Engine y Docker Compose con soporte para `!reset` (validado con 5.6.0),
Python 3.13 para herramientas locales y acceso al daemon Docker. La primera
instalación necesita acceso a los índices de imágenes y paquetes.
Ejecutar desde la raíz del repositorio. No hacen falta cuentas externas.

Imágenes oficiales fijadas por digest y dependencias Python por versión/hash.
Actualizarlas requiere revisión y pruebas, no se actualizan en cada arranque.

## Primer arranque de desarrollo

Generar el entorno **una sola vez**:

```bash
python3 scripts/setup_local.py development
```

Genera credenciales aleatorias independientes de bootstrap, migraciones y
aplicación en `.local/development/`, sin imprimir valores ni sobrescribir un
entorno existente. `.local/` está ignorado por Git y fuera del contexto de build.
No hay contraseñas predeterminadas.

Directorios locales: 0700; `compose.env`: 0600. Los archivos de secretos son
legibles por los UIDs de los contenedores en montajes de solo lectura; sus
padres 0700 impiden acceder a otros usuarios del host. No copiar valores a Git,
imágenes, comandos versionados o documentación. No regenerarlos con un volumen
inicializado: cambiar archivos no cambia las contraseñas de PostgreSQL.

Construir imagen, iniciar DB, aplicar migración y arrancar API, en este orden:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait db
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait backend
curl --fail --silent --show-error http://127.0.0.1:8000/health/live
```

Respuesta: `{"status":"ok"}`. Solo API en loopback:8000. PostgreSQL no publica
puertos al host. El Compose base tampoco publica la API; lo hace el override dev.
La API comprueba la conexión al arrancar y dispone su engine al detenerse.
Liveness indica vida del proceso, no disponibilidad continua de DB o versión de
esquema. Las migraciones no se ejecutan desde la API.

La revisión `0001_foundation` establece la cadena con `alembic_version`, sin
crear tablas comerciales. Consultar revisión:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
```

Detener conservando los datos de desarrollo:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml down
```

No agregar `--volumes` para conservar datos. Backups y producción siguen pendientes.

## Pruebas completas con PostgreSQL real

Generar las credenciales de pruebas una sola vez, distintas de desarrollo:

```bash
python3 scripts/setup_local.py test
```

Ejecutar en otro proyecto y con almacenamiento efímero:

```bash
docker compose --project-name carrillo-test --env-file .local/test/compose.env -f compose.yaml -f compose.test.yaml run --build --rm tests
```

Incluye unitarias e integración. DB en tmpfs, sin datos de desarrollo ni puertos
publicados. El runner exige entorno test, base `carrillo_test`, host interno y
roles esperados. Rechaza esquemas con tablas ajenas a esta fase.
No apuntarlo a una base operativa.

Terminar y reiniciar la siguiente ejecución desde DB vacía:

```bash
docker compose --project-name carrillo-test --env-file .local/test/compose.env -f compose.yaml -f compose.test.yaml down
```

El tmpfs se pierde al retirar el contenedor; las credenciales locales se conservan.

## Herramientas locales

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.lock
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pip check
sh -n ops/postgres/init.sh
git diff --check
```

Las pruebas locales simulan sus engines y no requieren DB. El runner Compose
comprueba conexión y rollback reales. Pytest usa tracebacks cortos y las conexiones
sanean fallos para evitar imprimir parámetros del controlador. No usar
`--showlocals` con secretos reales.

## Configuración

| Variable | Regla |
| --- | --- |
| `APP_ENV` | Obligatoria: `development`, `test` o `production`, sin valor de reserva. |
| `DOCS_ENABLED` | `false` por defecto; `true` prohibido en producción. |
| `DATABASE_URL` | URL completa `postgresql+psycopg`, tratada como secreto, sin reserva. |
| `DATABASE_URL_FILE` | Archivo alternativo a `DATABASE_URL`; Compose usa este modo. |

Definir exactamente una fuente de URL mediante entorno o `.env` local.
El esquema actual exige host, base, usuario y contraseña; rechaza otros drivers
y parámetros query. TLS remoto necesitará diseño explícito antes de habilitarse.
Las claves desconocidas de `.env` se rechazan. En Compose no hace falta `.env`:
se usan el archivo generado de interpolación y montajes de secretos.

`carrillo_app` no es superusuario ni puede crear bases/roles o hacer DDL en public.
`carrillo_owner` puede migrar ese esquema sin ser superusuario ni crear bases/roles.
La credencial bootstrap solo se monta en DB; la de migraciones no llega a la API.
Los contenedores Python usan UID 10001, filesystem de solo lectura, tmpfs y
capacidades retiradas.

El inicializador de roles solo se ejecuta al crear una DB vacía. Las futuras
tablas creadas por el owner conceden DML a la app. Esto no equivale a aislamiento
por tenant; ese control pertenece al hito 2.

## Actualizar dependencias

```bash
.venv/bin/pip-compile --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file backend/requirements.lock backend/pyproject.toml
.venv/bin/pip-compile --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --constraint backend/requirements.lock --extra dev --output-file backend/requirements-dev.lock backend/pyproject.toml
```

Revisar los locks y repetir validaciones. No versionar URLs de índices privados
con credenciales. Referencias: [SQLAlchemy PostgreSQL](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html),
[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[imagen PostgreSQL](https://hub.docker.com/_/postgres).

## Siguiente tarea

Fundación 1.3: Next.js/TypeScript mínimos, contenedor frontend y proxy de mismo
origen. Después CI del conjunto. Sin pantallas comerciales antes de acceso.
