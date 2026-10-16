# Entrega: CI reproducible (fundación 1.4)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/foundation-ci`.

## Resultado

- Workflow GitHub Actions para push, PR y ejecución manual, permisos mínimos de
  lectura y checkout v7.0.1 fijado al commit verificado de su repositorio oficial.
- Sin despliegue, publicación de imágenes, acceso a producción ni push remoto.
- Un único comando local/remoto: `python3 -m scripts.ci`.
- Proyecto, imágenes, credenciales y puerto proxy independientes de desarrollo.
- PostgreSQL efímero y migración explícita; validación de configuración antes de
  ejecutar los checks. Limpieza en finally, incluso ante fallos ordinarios.
- Validación de workflow mediante imagen actionlint fijada por digest.
- Seis pruebas del ejecutor: rechazo de proyecto dev, rechazo de workspace fuera
  del directorio ignorado, limpieza en fallo/éxito, entorno aislado y no sobrescritura.

## Archivos cambiados

- `.github/workflows/ci.yml`: permisos, eventos, límite de tiempo y comando común.
- `compose.ci.yaml`: imágenes por proyecto, puerto efímero y servicios de lint.
- `scripts/__init__.py`, `scripts/ci.py`, `scripts/tests/test_ci.py`.
- `scripts/setup_local.py`: permite un directorio base temporal sin cambiar el
  comportamiento de sus comandos development/test existentes.
- `README.md`, `docs/acceptance.md`, `docs/decisions.md` y este registro.

## Comandos exactos ejecutados

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
python3 -m unittest discover -s scripts/tests
python3 -m scripts.ci
git diff --check
```

El pipeline ejecutado incluye:

1. Configuración Compose resuelta: DB test/tmpfs, sin puertos de DB/API/frontend,
   proxy loopback efímero e imágenes separadas de desarrollo.
2. Pruebas unittest del ejecutor y `sh -n ops/postgres/init.sh`.
3. Build backend, tests backend, frontend y tests frontend.
4. actionlint del archivo workflow, sin acceso de red ni secretos de aplicación.
5. Ruff check/format de backend, migraciones, tests y scripts; `pip check`.
6. ESLint, Prettier, tipos Next/TypeScript y Vitest en contenedor sin red.
7. DB recién inicializada, Alembic upgrade head y pytest de backend.
8. Arranque API/frontend/proxy y smoke HTTP en el puerto CI asignado por Docker.
9. Limpieza de recursos y credenciales temporales, con etiquetas propias de imagen.

## Resultados reales

- Proceso `python3 -m scripts.ci`: **exit code 0**, mensaje final **CI passed**.
- **6 pruebas del ejecutor aprobadas**, sin invocar Docker desde esas pruebas.
- **36 pruebas backend aprobadas en 0.48s**, cinco con PostgreSQL real.
- **15 pruebas frontend aprobadas**; lint, formato y tipos correctos.
- Ruff: **All checks passed**, **16 files already formatted**.
- `pip check`: **No broken requirements found**. Aviso de caché pip no escribible,
  sin efecto en el resultado.
- Workflow validado por actionlint sin errores.
- Construcción de las cuatro imágenes completada; se reutilizó caché Docker de
  capas ya comprobadas. No se afirma una compilación nueva sin caché en este run.
- Smoke HTTP: HTML español, **9 assets**, API mismo origen, 404 y cabeceras correctos.
- Tras terminar: sin contenedores, redes, volúmenes, tags o carpetas temporales
  de ese proyecto CI. Se conservaron cachés compartidas y las imágenes base.
- Los IDs de los cuatro contenedores development fueron idénticos antes/después
  y sus cuatro health checks permanecieron saludables.
- No se mostraron ni versionaron las credenciales efímeras.

## Estado de aceptación y límites

La fundación está implementada y validada integralmente en local. La definición
del workflow está validada, pero **GitHub Actions no se ejecutó remotamente**:
`git remote -v` no devuelve ningún remoto. No se configura ni publica un repositorio
externo sin destino/autorización. La primera ejecución remota sigue pendiente.

El workflow usa un runner Ubuntu y la validación local requiere Linux, Docker
Compose con `!reset` y Python 3.11+ para la orquestación. Los lenguajes de aplicación
se ejecutan en imágenes fijadas. No se exige Node/Python de aplicación al host CI.

La limpieza se comprobó de forma real al finalizar con éxito y mediante pruebas
de propagación de error. No se promete recuperación ante SIGKILL, caída del host
o indisponibilidad del daemon. Un fallo Docker puede requerir retirar el proyecto
`carrillo-ci-...` identificado en la salida; nunca usar comandos sobre development.

No hay autenticación, módulos comerciales, HTTPS, backups ni despliegue público.
Tampoco se agregó E2E de negocio: no existe todavía ese flujo.

## Siguiente tarea

Acceso 2.1: modelo y migración de tenants, usuarios y membresías; restricciones
de identidad y primeras pruebas de aislamiento. CI remota se activará al definir
y autorizar el repositorio GitHub de destino. Entrega registrada en commit local.
