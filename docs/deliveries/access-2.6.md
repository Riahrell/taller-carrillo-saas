# Entrega: selección interna de empresa desde identidad verificada (acceso 2.6)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-company-selection`.

## Resultado

Servicio interno `begin_login`: verifica contraseña, estados activos y límites
compartidos antes de consultar empresas. Una membresía activa emite sesión desde
servidor; varias devuelven opciones y un estado provisional ligado al navegador.
Cero membresías o credenciales rechazadas devuelven el mismo rechazo genérico.
Se conserva la entrada interna anterior authenticate y sus pruebas.

Pendiente de cinco minutos, token aleatorio de 256 bits, únicamente huellas de
token/binding/hash Argon2 en PostgreSQL. No es una sesión ni concede permisos.
Una selección con binding correcto consume su único intento, también si propone
un UUID ajeno. Otro navegador no puede utilizarlo ni consumirlo. Se comprueban
pertenencia, usuario, empresa, membresía y rol actuales en servidor.

Consumo y emisión de sesión son una única transacción; los resultados solo salen
tras commit. Usuario bloqueado antes de pendientes/sesiones y reloj DB después
del lock. Dos selecciones simultáneas emiten exactamente una sesión. Cambios de
contraseña, caducidad o revocación mientras espera la segunda conexión se releen.

Una nueva autenticación correcta cierra pendientes anteriores del usuario. Un
rechazo de contraseña no los invalida. La huella se toma después del rehash del
propio login; cambiar posteriormente el hash, incluso de la misma contraseña,
invalida pendientes anteriores. Revocación por usuario disponible para la futura
administración, dentro de la transacción del cambio.

La tarea prevista se dividió para cerrar primero esta precondición de identidad.
HTTP y formulario se conectarán en las tareas siguientes. Esta entrega no añade
rutas públicas, cookies públicas nuevas, dependencias ni módulos comerciales.

## Archivos completos cambiados

- `backend/app/modules/access/login_challenges.py`: opciones, estado provisional,
  binding, caducidad, selección, consumo y revocación internos.
- `backend/app/modules/access/authentication.py`: begin_login/select_membership,
  verificación previa y transacciones con publicación posterior al commit.
- `backend/app/modules/access/models.py`: modelo LoginChallenge y restricciones.
- `backend/migrations/versions/0005_login_challenges.py`: tabla, FK restrictiva,
  unicidad, CHECK e índices por usuario/expiración; downgrade explícito.
- `backend/tests/integration/test_login_challenges.py`: 41 casos en PostgreSQL.
- `backend/tests/test_pending_login_inputs.py`: 11 casos de entrada malformada
  rechazados antes de consultar DB.
- `backend/tests/integration/test_postgres.py`: revisión esperada actualizada;
  conserva upgrade/check/downgrade/upgrade en la base efímera.
- `README.md`, `docs/auth-http-contract.md`, `docs/data-model.md`,
  `docs/decisions.md`, `docs/acceptance.md` y este registro: contrato, alcance,
  comandos, criterios y pendientes actuales.

## Comandos exactos ejecutados

Desde la raíz del repositorio:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
timeout 60 .venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
python3 -m scripts.ci
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
umask 077; docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T db sh -c 'pg_dump --format=custom -U "$POSTGRES_USER" "$POSTGRES_DB"' > .local/development/before-access-2.6.dump
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T db pg_restore --list < .local/development/before-access-2.6.dump > /dev/null
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
git diff --check
```

El comando de respaldo corresponde a esta ejecución previa a migrar. Conserva
ese archivo; no sobrescribirlo al repetir una validación. Es una copia privada
local dentro de .local ignorado, no un backup cifrado externo de producción.
No imprimirlo, versionarlo ni copiarlo al chat. Consultar README para instalación
nueva; estos comandos de desarrollo suponen el entorno local ya generado.

## Resultados reales

- Pipeline aislado `carrillo-ci-34f99fd4eacc`: **exit 0, CI passed**.
- Backend: **249 aprobadas en 10.37s**, desglosadas en 132 unitarias y 117 PostgreSQL.
- Frontend: **15 aprobadas**. Herramientas/configuración: **11 aprobadas**.
  **275 pruebas en el pipeline**, 52 casos nuevos respecto a acceso 2.5.
- Ruff y formato correctos en 46 archivos Python; pip check, actionlint,
  ESLint, Prettier y tipos correctos. Builds Docker ejecutados, con caché donde
  correspondía; no se presume construcción sin caché.
- Upgrade/check/downgrade completo/upgrade en la base efímera correctos.
- Pruebas de credenciales incorrectas, identidades/empresas/membresías inactivas,
  límites compartidos, selección de membresía ajena y UUID de empresa usado como
  membresía rechazados. No se devuelve lista antes de verificar contraseña.
- Se comprobaron consumo único, cookie de otro navegador, token provisional
  rechazado como sesión, rol actual, expiración exacta y creación futura.
- Fallos inyectados de insert/commit al iniciar y al seleccionar: sin resultado
  secreto ni efectos parciales; un consumo revertido permite reintentar.
- Tres casos de espera real de lock: contraseña cambiada, pendiente caducado y
  revocación; todos rechazan después de esperar. Otra prueba hace competir dos
  selectores y comprueba una sola sesión. Sin reemplazar PostgreSQL por SQLite.
- Constraints de hashes, plazos, FK y finalización comprobados en PostgreSQL.
- Unitarias locales fuera del sandbox: **132 aprobadas en 0.72s**, 117 integración
  excluidas por ese comando. No se suman otra vez al total del pipeline.
- El primer intento local detectó una colisión de nombres de módulos pytest;
  se renombró el archivo unitario. Otro intento dentro del sandbox quedó detenido
  al iniciar TestClient y se interrumpió (exit 130). La repetición fuera del
  sandbox y el pipeline Docker sí aprobaron; no se cuentan esos intentos como éxito.
- Desarrollo migrado a **0005_login_challenges (head)**; Alembic check devuelve
  **No new upgrade operations detected**. Se recreó backend, recargó Nginx y
  los cuatro servicios quedaron saludables.
- Respaldo anterior a migración: permisos 0600, 14671 bytes. pg_restore reconoce
  su catálogo (exit 0); no se realizó una restauración de esa copia en este paso.
- Smoke CI y local correctos: HTML español, nueve assets, API del mismo origen,
  404 y protecciones de escritura. La pantalla inicial sigue sin formulario activo.
- Sin cuentas reales creadas, credenciales regeneradas, exposición pública ni push.
  El runner retiró sus contenedores/redes y ejecutó su limpieza de recursos efímeros.

## Riesgos y pendientes

No hay todavía login/logout HTTP, emisor público de CSRF, cookie de desafío ni
formulario conectado. El único endpoint público de aplicación sigue siendo
liveness. Estas pruebas demuestran el servicio interno, no login por proxy.

El adaptador futuro debe verificar que browser_binding procede de una cookie
HttpOnly del servidor, además de origen/CSRF; la validación de formato actual no
acredita esa procedencia. Token pendiente en cookie HttpOnly, nunca JSON ni JS.
Si se reemplaza el binding durante el selector, se debe reiniciar el acceso.
Los límites generales de volumen/espera y hashing deben cerrarse antes de exposición.

IP confiable y falsificación de cabeceras por proxy real siguen pendientes;
no conectar un X-Forwarded-For arbitrario al servicio. Una opción de empresa no
autoriza módulos: cada operación futura necesita sesión viva y permisos actuales.

Un nuevo login correcto cancela selecciones pendientes del mismo usuario en
otros navegadores. Una opción inválida con binding correcto exige reiniciar login.
Estas son decisiones técnicas documentadas; la interfaz deberá explicarlas.
El pendiente no cambia tenant de sesiones existentes. Limpieza de pendientes,
contadores y sesiones aún pendiente. No hay RLS ni garantía frente a SQL arbitrario.

La futura administración debe bloquear usuario y revocar pendientes/sesiones junto
al cambio de contraseña/estado/privilegios correspondiente. Faltan último admin,
auditoría y recuperación. HTTPS real, backups cifrados/restauración, política fiscal
y hardening de producción continúan en sus hitos. GitHub CI remoto no se ejecutó:
el repositorio no tiene remoto configurado.

Referencias consultadas para autenticación genérica y bloqueos; los cinco minutos
y el intento único son decisiones del proyecto:
[OWASP autenticación](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[PostgreSQL 18](https://www.postgresql.org/docs/18/explicit-locking.html).

## Siguiente tarea

Acceso 2.7: adaptador HTTP de login/logout, emisor/renovador de CSRF y selección
usando el servicio verificado, con IP confiable y prueba de cabeceras falsificadas
por el proxy real. Después conectar formulario y probar el recorrido de navegador.
El hito 2 permanece abierto.
