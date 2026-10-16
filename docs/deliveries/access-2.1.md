# Entrega: empresas, usuarios y membresías (acceso 2.1)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-models`.

## Resultado

Tres tablas en PostgreSQL mediante migración explícita `0002_access`, sin semillas
ni credenciales predeterminadas. Identidad global por correo normalizado,
membresía única por empresa/usuario y rol obligatorio sin reserva privilegiada.
UUID, fecha UTC y estado activo en los tres modelos. CHECK y FK protegen también
las escrituras SQL directas; no hay borrado en cascada de identidades referenciadas.

El resolvedor interno devuelve una membresía solo si empresa, usuario y membresía
están activos. Comprueba la pareja usuario/empresa y vuelve a leer el rol en DB,
sin confiar en el objeto ORM previamente cargado. Usa la transacción del llamador
y no hace commit. No es un endpoint ni acepta contexto de una petición pública.

## Archivos cambiados

- `backend/app/modules/__init__.py`, `backend/app/modules/access/__init__.py`:
  módulo de acceso del monolito.
- `backend/app/modules/access/models.py`: modelos y restricciones.
- `backend/app/modules/access/membership.py`: resolución interna y error genérico.
- `backend/migrations/env.py`: registro de los modelos en metadata Alembic.
- `backend/migrations/versions/0002_access.py`: upgrade y downgrade explícitos.
- `backend/tests/integration/conftest.py`: engines compartidos con guardas de
  entorno, base, host, roles y tablas; transacción por prueba con rollback.
- `backend/tests/integration/test_access.py`: 22 casos nuevos en PostgreSQL real.
- `backend/tests/integration/test_postgres.py`: usa fixture compartido y verifica
  la nueva revisión y tablas, incluido ciclo upgrade/check/downgrade/upgrade.
- `README.md`, `docs/data-model.md`, `docs/acceptance.md` y este registro.

## Comandos exactos ejecutados

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
python3 -m scripts.ci
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
python3 scripts/smoke_web.py --base-url http://127.0.0.1:8080
git diff --check
```

Para un entorno nuevo, generar secretos e iniciar DB siguiendo README antes de
aplicar las migraciones. No ejecutar downgrade en una base con datos útiles: el
ciclo reversible se prueba exclusivamente en PostgreSQL efímero de CI.

## Resultados reales

- CI local integral `carrillo-ci-2c93257ee077`: **exit code 0**, **CI passed**.
- **58 pruebas backend aprobadas en 0.65s**, incluidas **27 con PostgreSQL real**:
  22 nuevas de acceso y cinco de infraestructura/migraciones.
- **15 pruebas frontend** y **6 pruebas del ejecutor** aprobadas: 79 en total
  dentro del pipeline, sin contar la repetición unitaria local.
- Lint/formato Python: correctos, **23 archivos**. Dependencias: `pip check`
  correcto. Lint/formato/tipos frontend y actionlint: correctos.
- Builds completados utilizando caché donde correspondía; no se afirma build
  sin caché. Arranque y smoke HTTP CI correctos. El ejecutor retiró sus recursos
  y credenciales temporales al terminar.
- Unitarias locales: **31 aprobadas en 0.30s**. La primera ejecución quedó
  bloqueada en TestClient dentro del sandbox y se interrumpió (exit 130); la
  repetición fuera del sandbox terminó correctamente. No se cuenta como pase
  el intento interrumpido.
- Desarrollo: migración aplicada, revisión **0002_access (head)**; cuatro servicios
  saludables. Smoke local correcto: HTML español, 9 assets, API del mismo origen,
  404 y cabeceras. Alembic check: **No new upgrade operations detected**.
- Pruebas nuevas: rol diferente por empresa, pertenencia inexistente, tres clases
  de desactivación, cambio de rol, correo canónico/duplicado, nombre/hash vacíos,
  rol inválido/ausente, membresía duplicada, FK inválidas, borrados referenciados
  y fallo transaccional sin identidades parcialmente persistidas.

## Riesgos y alcance pendiente

El hito de acceso sigue abierto. No hay login, sesiones, CSRF, autorización por
operación, límites de intentos ni administración HTTP. No usar en producción.
El resolvedor exige IDs procedentes de estado verificado del servidor; no permite
convertir IDs enviados por el navegador en una identidad autenticada.

No hay RLS: la credencial DB de aplicación conserva DML sobre las tablas. Estas
pruebas verifican el filtro del resolvedor, no aislamiento de SQL arbitrario.
Cada futuro módulo requerirá consultas filtradas y FK compuestas donde proceda.
Un contexto no debe mantenerse entre peticiones. Los cambios concurrentes de
privilegios y operaciones sensibles necesitarán el protocolo transaccional de
autorización, revocación y bloqueo en sus respectivos casos de uso.

El campo password_hash es solo almacenamiento; no verifica que su contenido sea
Argon2id. Los fixtures usan valores sintéticos que no son contraseñas ni hashes
de autenticación. La validación completa del correo, hashing, bootstrap seguro,
protección del último administrador y auditoría siguen pendientes.
La configuración monetaria y zona del tenant se añadirán al implementar sus
casos de uso conforme a las decisiones adoptadas; no se añadieron campos fiscales.

GitHub Actions todavía no se ejecutó remotamente: no hay remoto configurado.
No se hizo push ni despliegue público.

## Siguiente tarea

Acceso 2.2: Argon2id y comando CLI de bootstrap atómico de primera empresa,
usuario y membresía administradora. Entrada sensible sin contraseña en argumentos,
sin valores de reserva, sin alta pública y con pruebas de fallo/rollback y de
ejecuciones concurrentes. Después vendrán sesiones y contratos HTTP de acceso.
