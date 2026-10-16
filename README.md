# Taller Carrillo

Sistema web de gestión integral en construcción incremental. Monolito modular
FastAPI y Next.js, PostgreSQL y Docker Compose.

## Estado: fundación 1.4

API mínima, PostgreSQL privado, SQLAlchemy/Alembic, Next.js/TypeScript y proxy
del mismo origen, con pruebas aisladas. La pantalla inicial indica que el acceso
aún no está habilitado. Sin autenticación ni operaciones comerciales todavía.
La fundación tiene validación integral local y workflow CI preparado. Su primera
ejecución en GitHub está pendiente: no hay remoto configurado. No usar en producción.

- [Decisiones](docs/decisions.md), [modelo](docs/data-model.md) y [permisos](docs/permissions.md).
- [Aceptación](docs/acceptance.md) y [entrega 1.4](docs/deliveries/foundation-1.4.md).
- Las entregas 1.1/1.2 son registros históricos; el acceso actual usa el proxy
  en 8080, y el arranque requiere DB.

## Requisitos

Docker Engine y Docker Compose con soporte para `!reset` (validado con 5.6.0),
Python 3.13 para herramientas locales y acceso al daemon Docker. La primera
instalación necesita acceso a los índices de imágenes y paquetes.
Ejecutar desde la raíz del repositorio. No hacen falta cuentas externas.
Las herramientas npm locales requieren Node.js 24. Docker ya lo incluye, aunque
el Node instalado en el host tenga otra versión.

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
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend frontend frontend-tests
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait db
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait proxy
python3 scripts/smoke_web.py --base-url http://127.0.0.1:8080
```

Abrir [Taller Carrillo local](http://localhost:8080). La API está bajo `/api`:
`/api/health/live` devuelve `{"status":"ok"}`. Solo el proxy publica loopback:8080.
API, frontend y PostgreSQL no publican puertos propios al host. El Compose base
no publica ninguno; el override dev habilita el único puerto de entrada.
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

## Frontend y proxy

Next.js App Router con TypeScript estricto y salida standalone. La imagen Node
24 está fijada por digest y `npm ci` usa `package-lock.json` con integridades.
No se necesitan variables `NEXT_PUBLIC_*` ni recompilar una URL de API por entorno:
el cliente utiliza `/api/` relativo al navegador y el proxy retira ese prefijo.

El cliente HTTP actual es solo GET, con credenciales del mismo origen,
decodificador de respuesta y errores sin cuerpos internos. Las mutaciones se
añadirán junto con sesiones y CSRF en el hito 2, antes de habilitar negocio.
La pantalla inicial no inventa ventas, saldos ni datos de demostración.

Verificar frontend dentro del contenedor sin red:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm frontend-tests
python3 scripts/smoke_web.py --base-url http://127.0.0.1:8080
```

El primer comando ejecuta ESLint, Prettier, generación de tipos Next.js,
TypeScript y Vitest/React Testing Library. El segundo comprueba HTML, assets,
API, cabeceras y 404 a través del proxy. Antes, construir imágenes y arrancar
el entorno según el bloque de primer arranque.

Si se dispone de Node.js 24, los comandos locales equivalentes son:

```bash
cd frontend
npm ci
npm run lint
npm run format:check
npm run typecheck
npm test
npm run build
```

Compose ejecuta el build optimizado, sin hot reload. Después de editar frontend,
reconstruir `frontend`/`frontend-tests` y ejecutar `up -d --wait proxy` para aplicar
la imagen. Los tipos generados y cachés se excluyen de Git y del contexto Docker.
No hay fuentes ni imágenes remotas para esta pantalla.

Nginx y Next.js ejecutan sin root y con filesystem de solo lectura. Los archivos
temporales usan tmpfs. Nginx añade cabeceras básicas y su log de acceso omite query
strings, cuerpos y cookies. El acceso actual es HTTP local: HTTPS, CSRF y las
políticas completas de producción aún no están habilitados.

## Actualizar dependencias

```bash
.venv/bin/pip-compile --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file backend/requirements.lock backend/pyproject.toml
.venv/bin/pip-compile --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --constraint backend/requirements.lock --extra dev --output-file backend/requirements-dev.lock backend/pyproject.toml
```

Revisar los locks y repetir validaciones. No versionar URLs de índices privados
con credenciales. Referencias: [SQLAlchemy PostgreSQL](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html),
[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[imagen PostgreSQL](https://hub.docker.com/_/postgres).
Para el frontend: [salida standalone de Next.js](https://nextjs.org/docs/app/api-reference/config/next-config-js/output),
[Vitest con Next.js](https://nextjs.org/docs/app/guides/testing/vitest) y
[proxy Nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).

## CI y validación integral

Desde la raíz, con Docker/Compose y Python 3.11 o posterior para el ejecutor:

```bash
python3 -m scripts.ci
```

Es el mismo comando que usa `.github/workflows/ci.yml`. No necesita generar
entornos development/test previamente ni instalar Node o dependencias Python
de aplicación en el host. Los lenguajes de aplicación se ejecutan en sus imágenes
fijadas; el ejecutor solo usa la biblioteca estándar de Python.

El pipeline crea un proyecto `carrillo-ci-...` aleatorio con credenciales nuevas,
base PostgreSQL en tmpfs, imágenes con etiquetas propias y proxy en un puerto
loopback libre. Ignora variables locales de Compose/DB que podrían interferir.
No usa ni reinicia el proyecto `carrillo-dev` ni modifica sus contraseñas.

Valida, en orden:

1. Configuración de aislamiento, seis pruebas de seguridad del ejecutor y shell.
2. Builds de las cuatro imágenes de aplicación/pruebas.
3. Sintaxis de GitHub Actions con actionlint fijado por digest.
4. Ruff, formato Python y coherencia de dependencias; ESLint, Prettier y TypeScript.
5. Pruebas frontend, migración explícita y pruebas backend con PostgreSQL aislado.
6. Arranque del conjunto y smoke HTTP contra el proxy de ese proyecto.

El ejecutor retira contenedores, redes, volúmenes y credenciales temporales al
terminar. También intenta limpiar al propagarse un fallo; los tags de imágenes
propios se retiran sin borrar las imágenes fijadas o la caché compartida de build.
Un corte forzado del host/daemon no permite garantizar esa limpieza.

Solo las pruebas del ejecutor, sin Docker:

```bash
python3 -m unittest discover -s scripts/tests
```

El workflow se configura para push, pull request y ejecución manual. Tiene solo
`contents: read`, checkout fijado por commit y sin persistir credenciales; no
publica imágenes ni despliega. Tiene límite de tiempo y cancelación de ejecuciones
anteriores del mismo ref. Hasta el primer run real remoto, solo se puede afirmar
que el workflow pasó actionlint y el pipeline completo pasó localmente.

No hay remoto Git configurado en esta entrega. Activar CI remota requerirá conectar
el repositorio de destino y publicar los commits con autorización; no se hizo push.
Referencias: [sintaxis de GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[checkout](https://github.com/actions/checkout) y [actionlint](https://github.com/rhysd/actionlint).

## Siguiente tarea

Acceso 2.1: modelo y migración de empresas, usuarios y membresías, con restricciones
y primeras pruebas de aislamiento. La primera ejecución remota de CI permanece
pendiente hasta conectar un repositorio; sin negocio antes de autorización probada.
