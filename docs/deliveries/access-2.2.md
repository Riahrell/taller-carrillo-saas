# Entrega: Argon2id y bootstrap CLI (acceso 2.2)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-bootstrap`.

## Resultado

Comando de operador para crear la primera empresa, usuario global y membresía
administradora en una transacción. Exige esquema actualizado y las tres tablas de
acceso vacías, incluso registros inactivos. No reutiliza ni modifica identidades
existentes. El rol admin lo fija el servidor; no existe parámetro para escogerlo.

Bloqueo advisory de transacción con clave fija y timeout de 5s. Dos invocaciones
cooperantes se serializan: la segunda comprueba el estado después del commit de
la primera y falla sin escribir. Un fallo entre identidades y membresía revierte
todo y libera el bloqueo. No se añadieron tablas ni migraciones en esta entrega.

Contraseña oculta y confirmada en terminal, sin argumentos, env vars ni fallback
visible. EmailStr valida sintaxis; se conserva la política de correo en minúsculas.
Argon2id usa parámetros RFC_9106_LOW_MEMORY explícitos: m=65536 KiB, t=3, p=4,
sal aleatoria de 16 bytes y hash de 32 bytes. Utilidades de verificar y detectar
rehash disponibles para el futuro login. Contraseña de 15–128 caracteres Unicode,
sin recortar espacios ni aplicar reglas de composición.

No se creó una cuenta real ni se pidió una contraseña del operador. El recorrido
con escritura se ejecutó solo en PostgreSQL efímero de pruebas.

## Archivos cambiados

- `backend/app/modules/access/passwords.py`: hashing, verificación y rehash.
- `backend/app/modules/access/bootstrap.py`: entrada validada, transacción y CLI.
- `backend/pyproject.toml`, `backend/requirements.lock`,
  `backend/requirements-dev.lock`: argon2-cffi/email-validator y dependencias
  transitivas fijadas con hashes, manteniendo las versiones previas.
- `backend/tests/test_passwords.py`: cinco casos con Argon2 real.
- `backend/tests/test_bootstrap.py`: 19 casos de validación, CLI y saneamiento.
- `backend/tests/integration/test_bootstrap_postgres.py`: seis casos PostgreSQL.
- `README.md`, `docs/decisions.md`, `docs/data-model.md`, `docs/acceptance.md`
  y este registro.

## Comandos exactos ejecutados

Desde la raíz:

```bash
.venv/bin/pip-compile --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file backend/requirements.lock backend/pyproject.toml
.venv/bin/pip-compile --quiet --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --constraint backend/requirements.lock --extra dev --output-file backend/requirements-dev.lock backend/pyproject.toml
.venv/bin/python -m pip install --quiet --require-hashes -r backend/requirements.lock
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
python3 -m scripts.ci
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait proxy
docker compose run --help
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm backend python -m app.modules.access.bootstrap --help
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm --interactive --no-tty=false backend python -m app.modules.access.bootstrap
python3 scripts/smoke_web.py --base-url http://127.0.0.1:8080
git diff --check
```

La invocación interactiva local se **canceló con Ctrl+D en Empresa**, antes de
introducir datos: exit 2 y mensaje de cancelación esperado. La ayuda salió con
exit 0. Se verificaron las opciones con Compose instalado: `--interactive` y
`--no-tty=false`; no usar `-it`, que no está disponible en este Compose.
El README explica cómo completar el bootstrap en la terminal del operador.

## Resultados reales

- CI integral local `carrillo-ci-28640133a862`: **exit 0, CI passed**.
- **88 pruebas backend aprobadas en 1.09s**: 55 unitarias y 33 PostgreSQL.
- **15 frontend** y **6 del ejecutor** aprobadas: **109 pruebas en el pipeline**.
- Unitarias locales repetidas: **55 aprobadas en 0.49s**, 33 integración omitidas
  deliberadamente en ese comando. No se suman como casos distintos del pipeline.
- Ruff y formato: correctos, 28 archivos. `pip check`: sin incompatibilidades.
  actionlint, ESLint, Prettier y tipos: correctos.
- Builds y arranque del conjunto correctos; frontend reutilizó caché. Smoke CI y
  desarrollo: HTML español, 9 assets, API mismo origen, 404 y cabeceras correctos.
- Desarrollo con cuatro servicios saludables tras actualizar la imagen. Revisión
  de DB permanece `0002_access`; no hizo falta migrar ni modificar datos locales.
- Prueba concurrente: se observó la segunda conexión esperando en `pg_locks`, se
  liberó el primero y los resultados fueron una creación y un rechazo; conteos
  finales exactamente una empresa, un usuario y una membresía.
- Fallo inyectado antes de insertar membresía: conteos finales cero y reintento
  posterior correcto. Repetición, identidad inactiva y revisión antigua rechazadas.
- CLI probado con unitarias ante entrada sin TTY, contraseña distinta, eco,
  argumentos desconocidos y excepciones con un secreto canario: sin filtrarlo.
- El primer intento unitario tuvo un conflicto de nombres de módulos de pytest;
  se corrigió renombrando el archivo de integración y se repitió exitosamente.
  No se presenta aquel intento fallido como una validación aprobada.
- El ejecutor retiró contenedores/redes e imágenes propias de CI y sus secretos
  temporales al terminar. Desarrollo conserva sus datos y credenciales existentes.

## Riesgos y límites

El hito de acceso sigue incompleto: sin login, sesiones, CSRF, permisos de módulo,
auditoría ni límites de intentos. No usar en producción ni publicar la API.
El bootstrap no es recuperación de cuenta ni alta de empresas adicionales.
No se requiere crear ahora una cuenta real para continuar desarrollando sesiones.

El bloqueo coordina este comando bajo READ COMMITTED, no escrituras SQL manuales
ni migraciones simultáneas. El rol DB de aplicación sigue teniendo DML general;
no hay RLS ni protección contra un operador que modifique directamente las tablas.
La restricción de tabla comprueba hash no vacío, no formato Argon2id: todas las
futuras escrituras de contraseñas deben usar las utilidades del servidor.

EmailStr no acredita propiedad del correo ni entregabilidad. Faltan la protección
del último admin, revocación, recuperación y auditoría. Antes de exposición habrá
que medir Argon2id en el host destino y limitar trabajo concurrente para controlar
memoria/CPU, además de diseñar rechazo de contraseñas comprometidas. La detección
de rehash no actualiza DB automáticamente; eso pertenecerá al login transaccional.
Python no garantiza borrar las cadenas sensibles de memoria; la terminal, el
host y la configuración DB deben estar bajo control del operador.

GitHub Actions remoto sigue sin ejecutarse: no hay remoto configurado. Sin push
ni despliegue público. Referencias técnicas y decisiones en `docs/decisions.md`.

## Siguiente tarea

Acceso 2.3: modelo, migración y servicio interno de sesiones opacas revocables,
con token almacenado solo como hash, expiración absoluta/inactiva y comprobación
de membresía activa. Pruebas de aislamiento y revocación antes de incorporar
login/logout HTTP, CSRF y frontend de acceso en tareas posteriores.
