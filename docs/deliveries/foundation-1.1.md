# Entrega: fundación backend 1.1

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/foundation-backend`. El hito 1 completo sigue pendiente.

## Resultado

API FastAPI mínima con fábrica, configuración Pydantic v2 validada al crear la
aplicación y endpoint técnico público `GET /health/live`. Sin base de datos ni
operaciones comerciales. No es una comprobación de readiness.

Configuración requerida: `APP_ENV`. Documentación deshabilitada por defecto y
prohibida en producción. Errores de configuración sin valores de entrada.
No hacen falta secretos hasta incorporar las integraciones que los utilicen.

Dependencias runtime y de desarrollo fijadas con hashes SHA-256. Se resolvió
una incompatibilidad detectada en pruebas: Starlette 1.8 usa preferentemente
`httpx2`; se sustituyó la dependencia de desarrollo `httpx` por `httpx2`.
No se silenciaron advertencias: pytest trata warnings como errores.
Referencia: [TestClient de Starlette](https://starlette.dev/testclient/).

## Archivos de esta tarea

- Raíz: `.gitignore`, `.env.example`, `README.md`.
- Backend: `pyproject.toml`, `requirements.lock`, `requirements-dev.lock`.
- Aplicación: `app/__init__.py`, `app/main.py`, `app/core/__init__.py`,
  `app/core/config.py` dentro de `backend/`.
- Pruebas: `conftest.py`, `test_app.py`, `test_config.py` en `backend/tests/`.
- Documentación: este registro y actualización de estado en `decisions.md`
  y `acceptance.md`. Modelo y matriz de permisos anteriores sin cambios.

## Comandos ejecutados y resultados

Desde la raíz del repositorio, con Python 3.13.5:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir 'pip-tools>=7,<8'
.venv/bin/pip-compile --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file backend/requirements.lock backend/pyproject.toml
.venv/bin/pip-compile --quiet --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --constraint backend/requirements.lock --extra dev --output-file backend/requirements-dev.lock backend/pyproject.toml
.venv/bin/pip-sync --pip-args='--require-hashes --no-cache-dir' backend/requirements-dev.lock
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests
.venv/bin/python -m ruff check --config backend/pyproject.toml backend
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend
.venv/bin/python -m pip check
.venv/bin/python -m build --wheel --no-isolation --outdir /tmp/taller-carrillo-dist backend
git diff --check
```

- Pruebas: **18 passed in 0.18s** en la ejecución fuera del sandbox.
- Ruff: **All checks passed!**; **7 files already formatted**.
- Dependencias: **No broken requirements found**.
- Wheel: **Successfully built taller_carrillo_backend-0.1.0-py3-none-any.whl**.
- Verificación de diff: sin errores reportados; complementada con revisión
  de espacios finales de archivos nuevos porque aún no están versionados.
- Lock runtime: 16 dependencias; versiones compatibles con las del lock dev.

Se arrancó el proceso real:

```bash
APP_ENV=development .venv/bin/python -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

En otra ejecución:

```bash
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health/live
```

Resultado HTTP: `{"status":"ok"}`. Servidor detenido al terminar la comprobación.

## Incidencias del entorno

- La instalación inicial falló por resolución de red del sandbox. Se reintentó
  con permiso fuera del sandbox y se instalaron los paquetes en `.venv`.
- El cambio de rama requirió permiso por el montaje de `.git` en solo lectura.
- La primera recogida de tests detectó la deprecación de `httpx`. Tras sustituir
  la dependencia y sincronizar el entorno, se repitieron las validaciones.
- El sandbox impidió enlazar el puerto local y consultar el servidor externo
  a su aislamiento. El arranque y petición HTTP se validaron fuera del sandbox.
- El TestClient quedó detenido bajo aislamiento; se interrumpió esa ejecución
  y las 18 pruebas pasaron al reintentar fuera del sandbox.
- `pip check` emitió un aviso por caché no escribible, sin dependencias rotas.
- Docker Compose está instalado. La consulta exploratoria al daemon fue
  denegada por permisos; no se validó Docker ni se inició PostgreSQL.

## Límites y riesgos pendientes

- Sin autenticación: no existe todavía ningún endpoint de negocio. No añadirlos
  antes de implementar protección por defecto y probar autorización/tenants.
- Sin PostgreSQL, SQLAlchemy, Alembic, frontend, Dockerfiles, Compose o CI todavía.
- Configuración y ejecución actuales comprobadas en Linux con Python 3.13.5.
- No hay pruebas de inventario/dinero porque no existen esos módulos.
- Política fiscal sigue pendiente antes de cobro real y producción.
- Archivos nuevos y cambios quedan disponibles para revisión; no se creó commit.

## Siguiente tarea

Fundación 1.2: PostgreSQL privado, SQLAlchemy 2.x, Alembic, configuración de DB
sin credenciales de reserva y pruebas de conexión/migración en base aislada.
Será necesario acceso autorizado al daemon Docker para ejecutar esa validación.
