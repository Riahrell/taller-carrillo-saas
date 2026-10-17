# Taller Carrillo

Sistema web de gestión integral en construcción incremental. Monolito modular
FastAPI y Next.js, PostgreSQL y Docker Compose.

## Estado: acceso 2.30, prueba de buzón MFA interna; web sin segundo factor

API mínima, PostgreSQL privado, SQLAlchemy/Alembic, Next.js/TypeScript y proxy
del mismo origen, con pruebas aisladas. La pantalla inicial permite login,
selección de empresa y logout con cookies privadas. Modelos de empresas,
usuarios y membresías migrados, con
consulta interna de membresía activa probada en PostgreSQL. Sesiones internas
revocables con hash de token y expiración probadas. Autenticación interna con
límites compartidos por cuenta/IP. La API ya exige origen/CSRF firmado en
peticiones con efectos y tiene política de cookies probada. La selección interna
multiempresa exige contraseña verificada y un estado temporal ligado al navegador,
con caducidad y uso único probados. Login/logout, selección, consulta de sesión
y emisión/renovación CSRF ya funcionan por HTTP con cookies privadas. La IP se
obtiene de Nginx verificado en una red interna separada, con pruebas reales de
cabeceras falsificadas. Formulario probado en navegador con identidades sintéticas
y PostgreSQL aislado. El servicio interno de administración ya verifica sesión
y rol actual, cambia rol/estado de una membresía, conserva un administrador activo
y confirma auditoría/revocaciones en una transacción. Ya tiene consulta y cambio
por HTTP con sesión/CSRF y precondición If-Match. El listado paginado ya limita
resultados a la empresa autorizada y al perfil admin. La pantalla `/membresias`
permite revisar y confirmar cambios sobre accesos existentes. Las operaciones
comerciales siguen pendientes. El servicio interno ya puede crear una cuenta
nueva y su membresía con autorización y auditoría atómicas. Ya está conectado a
POST administrativo con sesión/CSRF y contexto obligatorio. El formulario de
alta asistida ya está en `/membresias/nueva`, con revisión y confirmación separadas.
Bootstrap CLI atómico y hashing Argon2id
disponibles; no se creó una cuenta real automáticamente.
La fundación tiene validación integral local y workflow CI preparado. Su primera
ejecución en GitHub está pendiente: no hay remoto configurado. No usar en producción.

- [Decisiones](docs/decisions.md), [modelo](docs/data-model.md) y [permisos](docs/permissions.md).
- [Aceptación](docs/acceptance.md), [última entrega 2.30](docs/deliveries/access-2.30.md)
  y [contrato de diseño 2.20](docs/deliveries/access-2.20.md).
- [MFA y recuperación](docs/mfa-recovery-contract.md): TOTP, incorporación,
  códigos offline y separación de recuperación por correo. Primitivas internas
  implementadas en [2.21](docs/deliveries/access-2.21.md), persistencia/consumo
  transaccional en [2.22](docs/deliveries/access-2.22.md) y auditoría global
  de consumos en [2.23](docs/deliveries/access-2.23.md). [Cuotas 2.24](docs/mfa-verification-contract.md)
  independientes de contraseña; integración de autenticación pendiente.
- [Escaneos de seguridad](docs/security-scanning.md): secretos en archivos/historial,
  dependencias Python/npm y excepción temporal dev-only visible hasta 2026-10-31.
- [Contrato de confirmación reciente](docs/reauthentication-contract.md): servicio
  y POST `/api/auth/reauthenticate`, formulario y guardia obligatoria dentro de
  las transacciones de alta/cambio administrativos. Sin prueba vigente: 428.
- [Revisión de seguridad](docs/security-review.md) y [pruebas remotas](docs/staging-plan.md).
- [Recuperación por correo](docs/email-recovery-design.md): diseño solicitado,
  pendiente de implementación junto con verificación de correo y MFA.
- [Revisión del nuevo Plan Maestro v1.1](docs/reviews/plan-maestro-v1.1.md):
  diferencias, mejoras propuestas y adaptación de hitos; revisión documental,
  sin implementación del alcance nuevo ni cambios de servidor.
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

Si el entorno existía antes de acceso 2.5, añadir la clave HTTP **sin regenerar
credenciales DB** (comandos idempotentes para entornos ya creados):

```bash
python3 -m scripts.setup_http development
python3 -m scripts.setup_http test
```

El segundo requiere que exista el entorno test; si aún no existe, créalo con
setup_local.py test como se indica abajo. Se añade solo la nueva clave aleatoria
csrf_signing_key y PUBLIC_ORIGIN si falta. No sobrescribe ninguna credencial.

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

La revisión actual `0007_membership_creation_audit` amplía la auditoría de
`0006_membership_audit` con eventos tipados de alta. Ambas se apoyan en
`0005_login_challenges` (selección temporal de empresa),
`0004_login_throttles` (contadores), `0003_sessions` (sesiones) y
`0002_access` (identidades). No inserta cuentas. Los roles
son `admin`, `cashier`, `mechanic` y `read_only`, sin rol predeterminado.
La consulta interna de membresía comprueba los tres estados activos en DB;
requiere IDs obtenidos por el servidor y no concede permisos de módulo.
El contrato de acceso HTTP está en docs/auth-http-contract.md. Argon2id se usa
en el bootstrap CLI; la creación inicial se ejecuta únicamente por el operador.
Los servicios de sesión y selección ya se conectan a la capa HTTP.
Una selección pendiente no autoriza módulos ni sustituye una sesión activa.
Consultar revisión:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
```

## Crear el primer administrador

Con DB migrada y la imagen backend actualizada, ejecutar **en tu terminal**:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm --interactive --no-tty=false backend python -m app.modules.access.bootstrap
```

Solicita nombre de empresa y correo; después contraseña y confirmación ocultas.
No escribas contraseñas en este chat, argumentos, variables de entorno ni archivos
versionados. Exige TTY y aborta si no puede ocultar la entrada. No hay alta pública
ni contraseña de reserva. No se creó un usuario de demostración en desarrollo.

Correo validado sin comprobar entrega por DNS y normalizado a minúsculas.
Contraseña de 15–128 caracteres Unicode, sin recorte ni reglas de composición;
los espacios forman parte de la contraseña. Hash Argon2id: 64 MiB, tres pasadas,
cuatro vías, sal aleatoria de 16 bytes y resultado de 32 bytes. No se imprime
la contraseña, el hash ni el correo en el resultado del comando.

El comando usa el rol DB de aplicación, requiere la revisión Alembic actual y
rechaza cualquier registro de acceso existente, incluso inactivo. Crea empresa,
usuario y membresía administradora en un commit; un fallo revierte los tres.
Un bloqueo transaccional coordina otros bootstraps, con espera máxima de 5s.
Una repetición falla sin modificar datos ni restablecer contraseñas. No borres
registros para repetirlo; recuperación y administración se implementarán después.
No ejecutar junto con migraciones o escrituras SQL manuales.

Ayuda, sin crear registros:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm backend python -m app.modules.access.bootstrap --help
```

Códigos de salida: 0 completado/ayuda, 1 fallo de operación y 2 entrada inválida
o cancelación. Los handlers de acceso ya emiten cookies y pruebas CSRF; el
formulario web ya los utiliza. Límites compartidos, cookies y revocación se
validan también por proxy real en el CI aislado.
Después del bootstrap, ingresar en http://localhost:8080 con esos datos. No se
crearon usuarios reales automáticamente ni existe una contraseña de reserva.

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
| `PUBLIC_ORIGIN` | Origen canónico exacto del navegador, sin barra final/ruta/query. Obligatorio para arrancar API; HTTPS en producción. |
| `CSRF_SIGNING_KEY_FILE` | Archivo de clave externa de 32 bytes aleatorios, hex minúsculo (64 caracteres). Compose usa este modo. |
| `CSRF_SIGNING_KEY` | Alternativa al archivo, tratada como SecretStr; nunca definir ambas fuentes ni usar una reserva. |
| `TRUSTED_PROXY_HOST` | Identidad DNS del proxy en red api interna. Obligatoria al arrancar HTTP; Compose define proxy. No usar una cabecera o IP aportada por el cliente. |

Definir exactamente una fuente de URL mediante entorno o `.env` local.
El esquema actual exige host, base, usuario y contraseña; rechaza otros drivers
y parámetros query. TLS remoto necesitará diseño explícito antes de habilitarse.
La clave HTTP se monta en backend/tests, no en DB, frontend ni migraciones.
Los comandos CLI/migraciones no requieren origen ni clave HTTP; create_app sí.
Las claves desconocidas de `.env` se rechazan. En Compose no hace falta `.env`:
se usan el archivo generado de interpolación y montajes de secretos.

`carrillo_app` no es superusuario ni puede crear bases/roles o hacer DDL en public.
`carrillo_owner` puede migrar ese esquema sin ser superusuario ni crear bases/roles.
La credencial bootstrap solo se monta en DB; la de migraciones no llega a la API.
Los contenedores Python usan UID 10001, filesystem de solo lectura, tmpfs y
capacidades retiradas.

El inicializador de roles solo se ejecuta al crear una DB vacía. Las futuras
tablas creadas por el owner conceden DML a la app. Esto no equivale a aislamiento
por tenant: el SQL directo con la credencial app no se limita a una empresa.
La consulta interna de acceso filtra por identidad y empresa; los futuros
endpoints deberán obtenerlos de una sesión verificada y aplicar los permisos.
El hito 2 sigue incompleto y no se debe exponer el sistema públicamente.

## Sesiones internas (acceso 2.3)

`app.modules.access.sessions` permite emitir, resolver y revocar sesiones dentro de
una transacción del llamador. La emisión exige credenciales ya verificadas por
el servidor y una membresía activa; no constituye un login por sí misma. El
futuro login debe bloquear el usuario antes de verificar su contraseña y mantener
ese bloqueo hasta commit. Nunca publicar el token antes de confirmar la transacción.

Token generado con 32 bytes aleatorios (256 bits), representado como SecretStr;
solo SHA-256 se persiste. Empresa y rol se obtienen del servidor. La FK compuesta
impide una sesión con membresía de otra empresa. Cada resolución consulta los
estados activos y el rol vigente; los servicios no conceden permisos de módulo.

Caducidad exacta: 30 minutos sin actividad o 8 horas desde creación. La actividad
válida actualiza last_seen_at sin mover expires_at. El reloj es clock_timestamp()
de PostgreSQL leído tras los bloqueos. Una sesión revocada/expirada se rechaza
con error genérico. La revocación individual es idempotente.

Todas las operaciones coordinadas bloquean primero usuario y luego sesiones por
UUID; se retienen hasta commit/rollback. Esto serializa operaciones de un mismo
usuario: mantener transacciones cortas y no esperar red externa dentro de ellas.
Los futuros cambios de contraseña deben llamar revoke_user_sessions y
revoke_user_challenges en su misma transacción; los de
privilegios revoke_membership_sessions en su misma transacción. El SQL manual no
revoca sesiones automáticamente. No hay endpoints para estas funciones todavía.

## Autenticación interna (acceso 2.4)

`Authenticator` verifica credenciales, membresía y estado activo bajo bloqueo del
usuario; crea la sesión y la devuelve solo después de confirmar. Posee su propia
transacción para que los rechazos persistan sus contadores. None representa el
mismo rechazo para todos los casos ordinarios. Un fallo de DB/hashing se traduce
a AuthenticationUnavailable saneado; nunca se devuelve un token sin commit.

Límites globales de 5 fallos por correo normalizado y 30 por IP en ventanas fijas
de 15 minutos. El primer fallo inicia cada ventana; alcanzar el límite impide los
intentos siguientes hasta su vencimiento, incluso con contraseña correcta. Las
peticiones bloqueadas no extienden el plazo. Un éxito reinicia la cuenta y conserva
los fallos de la IP compartida. No se bloquea permanentemente un usuario.

Orden de bloqueo de login: IP, cuenta, usuario, pendientes y sesiones. Los contadores son PostgreSQL,
compartidos entre workers; solo el hash del correo/IP se guarda como clave. Eso
no anonimiza los valores, que pueden ser deducibles. IPv4 y su representación
IPv6 mapeada comparten contador. La IP debe venir de un adaptador confiable.
Un origen bloqueado no genera filas para nuevos correos ni ejecuta Argon2.

Se inicializa una instancia por worker antes de admitir credenciales, con un
hash de descarte aleatorio para cuentas inexistentes/hashes inválidos. No se
promete tiempo constante de extremo a extremo. Rehash de perfiles Argon2id
anteriores y sesión nueva se confirman en la misma transacción.

El servicio está conectado al servidor HTTP y a la pantalla de acceso. Consultar
[contrato HTTP de seguridad](docs/auth-http-contract.md), con partes implementadas
y pendientes explícitos.
La selección de empresa es una candidatura interna sujeta a membresía; el contrato
de Credentials rechaza tenant, rol e identidad elegidos por cliente.

## Selección interna de empresa (acceso 2.6)

begin_login verifica credenciales antes de obtener empresas. Una membresía activa
permite emitir sesión; varias devuelven opciones y PendingLogin, ligado a una
cookie de navegador que el adaptador HTTP verifica. Cero rechaza de
forma genérica. No hay lookup público de empresas por correo.

El pendiente dura cinco minutos y guarda solo hashes de token, binding y hash
Argon2 actual. No autoriza módulos. select_membership deriva usuario desde el
pendiente y tenant/rol desde una membresía actual que le pertenece. Un intento
con binding correcto consume el pendiente, incluso con opción inválida; otro
navegador no puede usarlo ni consumirlo. Consumo y sesión confirman juntos.

Un nuevo login correcto cancela pendientes previos; una contraseña incorrecta
no los cancela. Cambio de hash de contraseña o revocación invalida el pendiente.
Los servicios están probados con concurrencia PostgreSQL y selección mediante
Nginx real. Detalles internos en la [entrega 2.6](docs/deliveries/access-2.6.md)
y transporte en la [entrega 2.7](docs/deliveries/access-2.7.md).

## Acceso HTTP (acceso 2.7)

La API rechaza peticiones con efectos si Origin no coincide exactamente con
PUBLIC_ORIGIN, falta prueba CSRF o el nonce/sesión ligado a la firma no coincide.
La ruta GET /api/auth/csrf emite/renueva pruebas y exige X-Requested-With:
XMLHttpRequest. La misma cabecera protege GET /api/auth/session. CORS permanece
cerrado; si Origin/Sec-Fetch-Site existen, deben corresponder al mismo origen.

POST /api/auth/login acepta solo email/password; POST /api/auth/select-membership,
solo membership_id; POST /api/auth/logout requiere {}. Las tres escrituras
exigen Origin y X-CSRF-Token válidos. Login devuelve 200 o 202 con opciones de
empresa; los tokens van solo en cookies HttpOnly. Logout revoca en DB antes de
borrar cookies. Debe cerrarse sesión antes de iniciar otro acceso. Ver
[contrato completo](docs/auth-http-contract.md) para respuestas, límites y errores.

La IP de login se acepta solo si el socket peer coincide con TRUSTED_PROXY_HOST
resuelto en DNS Docker. Backend/proxy comparten api interna; frontend usa edge y
no comparte red con backend. Nginx sobrescribe X-Real-IP y elimina cabeceras
Forwarded/X-Forwarded-For/X-Forwarded-Host aportadas por el cliente. Uvicorn conserva
el socket real con --no-proxy-headers. No sustituirlo por confianza en toda IP privada.

Perfil inicial: Nginx 10 peticiones/s por IP en /api/auth/, burst 20 y diez
conexiones activas por IP; dos verificaciones de contraseña por worker y 64
conexiones/tareas Uvicorn. Exceso general: 429 del proxy o 503 por capacidad.
DB limita espera de lock a 3s y sentencia a 10s por transacción de acceso.
Los límites de fallos por cuenta/IP conservan 401 genérico. Falta medir capacidad
para producción; no se promete un timeout total de HTTP/DNS.

Para desarrollo se generó PUBLIC_ORIGIN=http://localhost:8080. Abrir esa dirección:
http://127.0.0.1:8080 representa otro origen. Cambiar PUBLIC_ORIGIN exige actualizar
la configuración y recrear API. Producción requiere HTTPS y usa cookies __Host-,
Secure, HttpOnly para sesión/preauth/pendiente, Path=/, SameSite=Lax y sin Domain. El cookie
CSRF es legible por JS y contiene solo la prueba firmada, nunca el token de sesión.

HMAC-SHA-256 separa propósitos y liga la prueba a una cookie HttpOnly de sesión o
nonce preauth. Nonce y prueba caducan en 15 minutos; al emitir sesión se cambia la
prueba y se retira el nonce preauth. Renovar CSRF no extiende expires_at de sesión.
La clave debe ser común a todos los workers y reiniciar coordinadamente al rotarla.
El nonce preauth protege intención del navegador: **no acredita identidad**.

El middleware no sustituye autenticación ni autorización: cada futuro handler
protegido deberá comprobar sesión viva en DB y permisos, aunque el CSRF sea válido.
La emisión de cookies de sesión solo podrá ejecutarse tras commit de autenticación.

Por ahora, las escrituras de API requieren JSON UTF-8 de hasta 8 KiB, verificado
también en cuerpos fragmentados. Nginx impone el mismo límite en /api/. Formularios,
cuerpos inválidos/grandes y pruebas CSRF ambiguas se rechazan antes del handler.
Los futuros adjuntos necesitarán parser/límite explícitos y las mismas protecciones
de origen/CSRF; no quedan habilitados por esta política JSON.

Los errores de validación no repiten inputs de Pydantic. Rechazos de transporte y
escrituras llevan Cache-Control: no-store. Detalle en el contrato actualizado.
Después de cambiar el proxy, aplicar su config y la imagen conservando los datos:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --force-recreate proxy
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

## Frontend y proxy

Next.js App Router con TypeScript estricto y salida standalone. La imagen Node
24 está fijada por digest y `npm ci` usa `package-lock.json` con integridades.
No se necesitan variables `NEXT_PUBLIC_*` ni recompilar una URL de API por entorno:
el cliente utiliza `/api/` relativo al navegador y el proxy retira ese prefijo.

El cliente HTTP usa GET/POST con credenciales del mismo origen, respuestas
decodificadas y errores sin cuerpos internos. Antes de cada escritura de acceso
renueva CSRF y lee únicamente la prueba legible del perfil HTTP/HTTPS. No fija
Origin ni Cookie desde JS, no acepta redirects y no reintenta escrituras.
Cada request tiene espera máxima de 15s en cliente; abortar no deshace un commit.
Un resultado incierto ofrece comprobar sesión mediante lectura antes de reintentar.

El panel comprueba sesión antes de habilitar el formulario. Contraseña fuera de
estado React y borrada del campo al enviar; email/password solo en JSON, nunca
URL, localStorage o sessionStorage. El formulario usa POST también como método
HTML. La política de login conserva contraseña sin recortes y admite 1–128
caracteres Unicode; no impone el mínimo de alta a cuentas existentes.

Varias empresas requieren selección explícita, sin primera opción automática.
El contexto activo solo aparece tras GET de sesión verificada. Logout/cancelación
retiran contexto y opciones, y solo anuncian cierre al confirmar el servidor.
Se revalida al recuperar foco/visibilidad o recibir una señal entre pestañas,
sin compartir datos de identidad por el canal ni hacer polling. Tras cinco minutos
se retira un selector visible y tras treinta, el contexto visible; estos timers
no autentican, renuevan ni revocan una sesión de servidor.
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
strings, cuerpos y cookies. El acceso actual es HTTP local: HTTPS y las políticas completas de producción
aún no están habilitados. El middleware y los handlers de acceso están conectados;
el formulario obtiene/envía las pruebas. Uvicorn no registra
access logs; CI verifica ausencia de un canario sintético en logs de proxy/API.

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

1. Configuración de aislamiento, once pruebas de ejecutor/configuración y shell.
2. Builds de las cuatro imágenes de aplicación/pruebas.
3. Sintaxis de GitHub Actions con actionlint fijado por digest.
4. Ruff, formato Python y coherencia de dependencias; ESLint, Prettier y TypeScript.
5. Pruebas frontend, migración explícita y pruebas backend con PostgreSQL aislado.
6. Arranque, origen ajustado al puerto CI real y recarga Nginx sin reiniciarlo.
7. Smoke de lecturas, rechazo 403 sin origen/CSRF válidos y 413 por tamaño.

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

## Administración de membresías (2.9–2.14)

`MembershipAdministrator.change_membership` recibe el token privado de sesión,
el UUID objetivo y un `MembershipChange` explícito de rol/estado. El servidor
resuelve actor, empresa y permiso admin actual dentro de su transacción. El
adaptador HTTP y la pantalla usan ese servicio; no hay CLI operativo. No llamarlo desde
otra transacción ni adaptar identidad/tenant desde campos del navegador.

Bloquea empresa, todos los usuarios implicados por UUID y luego sus registros de
acceso. No degrada ni desactiva al último administrador activo. Un cambio efectivo
revoca sesiones de la membresía, cancela pendientes globales del usuario y registra
un evento tipado en `audit_events`, todo en un commit. La aplicación puede insertar
y consultar esa tabla, pero no actualizarla ni borrarla; el propietario DB conserva
autoridad administrativa. Sin RLS ni defensa frente a SQL arbitrario privilegiado.
La auditoría cubre estos cambios y las altas internas 2.13; su consulta HTTP y otras acciones
se implementarán en tareas posteriores. Detalle y resultados en entrega 2.9.

Contrato actual: [administración HTTP](docs/membership-http-contract.md).
GET `/api/auth/memberships/{membership_id}` devuelve únicamente ID, rol y estado
de la membresía de la empresa autorizada, con ETag y no-store. PATCH a esa ruta
exige sesión, origen/CSRF, JSON role/is_active explícitos y un único If-Match con
el tag revisado. La comparación se realiza bajo bloqueo antes de escribir;
un estado diferente devuelve 412 sin sobrescribirlo. No hay concesión de permisos
por enviar un rol. Si el actor modifica su propio acceso, se borran cookies solo
tras el commit que revoca su sesión. Fallo de commit conserva cookies y devuelve
503; no reintentar automáticamente. Las rutas no aceptan query params.

GET `/api/auth/memberships` lista ID, correo, rol y estados de membresía/cuenta
existentes, solo para administradores de la empresa de la sesión. Query opcional
limit (25 por defecto, 1–100) y after (UUID), sin parámetros extra o duplicados.
Orden UUID ascendente, respuesta items/next_after y no-store. La página siguiente
lee estado actual; no es una exportación congelada ni un permiso obtenido del cursor.
Antes de editar se consulta el detalle y su ETag. No se expone hash/token ni otras
empresas del usuario. El listado aprovecha (tenant_id, id) existente, sin columnas nuevas.

La sesión admin muestra el enlace a `/membresias`. La pantalla vuelve a comprobar
sesión y perfil, muestra hasta 25 accesos por página y consulta el detalle antes
de editar rol/estado. Exige revisar el resumen y confirmar; un 409/412 exige otra
lectura y decisión. Las escrituras renuevan CSRF y envían If-Match y
X-Expected-Membership. Esta última compara el actor esperado con el autenticado,
sin elegir actor ni empresa. Todas las respuestas exitosas de administración
incluyen X-Access-Membership para descartar resultados de otro contexto de sesión.

Ante acceso perdido, respuesta incierta, cambio de visibilidad o señal entre
pestañas se retira el contexto y se comprueba de nuevo. Sin reintentos de PATCH,
almacenamiento persistente de datos o polling. Las comprobaciones del navegador
no sustituyen la autorización del servidor. Sin cambios de usuario global
ni consulta HTTP de auditoría en esta fase. No se necesitan usuarios reales nuevos
para ejecutar el pipeline.

QA manual aislado: `python3 -m scripts.browser_qa`. Construye imágenes, crea una
base efímera y muestra una URL loopback con puerto dinámico y el nombre de un
contenedor de fixture. Las credenciales sintéticas quedan en el archivo privado
`/tmp/carrillo-browser-fixture.json` de ese contenedor, nunca en stdout. Terminar
con `docker exec NOMBRE_DEL_CONTENEDOR touch /tmp/carrillo-browser-finished`;
el runner elimina sus recursos. Caduca a los 30 minutos. No usar datos reales.

Alta interna 2.13: `MembershipAdministrator.create_user` acepta `UserCreation`
(email, password SecretStr, role explícito) y expected_actor obligatorio. Tenant,
permiso y estados activos se resuelven en servidor. Solo cuentas nuevas: correo
global ya registrado produce el mismo conflicto, sin vincular ni resetear esa
identidad. Usuario, membresía y evento `membership_created` se confirman juntos.
No emite sesión ni entrega contraseña. Ver
[contrato de alta](docs/user-provisioning-contract.md).

POST `/api/auth/memberships` crea la cuenta nueva y su membresía. Exige sesión
admin actual, CSRF/origen y X-Expected-Membership obligatorio. JSON email/password/
role, sin query ni campos extra. Devuelve 201 tras commit con ID de membresía,
rol/estado, ETag, X-Access-Membership y Location relativo al detalle. Sin cookies
nuevas, hash o contraseña en la respuesta. Correo existente: 409 genérico, sin
vincular ni resetear. Comparte los dos slots de hashing por proceso con login,
los límites del proxy y no-store. No reintentar POST automáticamente.

Canal previsto para el formulario: alta presencial/asistida en un equipo
controlado por el administrador, donde la persona nueva escribe la contraseña
que elige en un campo oculto. Sin envío de claves por chat/correo/URL ni contraseña
predeterminada. HTTPS fuera de loopback según configuración. Este canal requiere
confianza en el equipo y administrador; no implementa invitaciones remotas,
verificación de correo ni recuperación.

En `/membresias/nueva`, elegir correo y perfil sin valor predeterminado; después
revisar el resumen y escribir/confirmar la contraseña en campos ocultos no
controlados por estado React. Se retiran al enviar, volver a editar, cancelar o
perder contexto. La contraseña no se muestra en el resumen ni se guarda en Storage.
El éxito requiere 201, sesión admin todavía coincidente y detalle releído.
409/422 exigen otra revisión; otros fallos retiran contexto y no repiten POST.
No se garantiza borrado criptográfico de strings JavaScript ni control del gestor
de claves/extensiones del navegador del operador.

La migración 0007 conserva eventos anteriores y privilegios de auditoría. Si hay
eventos de alta, un downgrade a 0006 se rechaza para conservar evidencia que el
esquema anterior no puede representar. No borrar registros para forzarlo.

## Siguiente tarea

Acceso 2.21 agrega la biblioteca interna TOTP, cifrado de semillas y códigos offline,
con dependencias fijadas y pruebas. Detalles en `docs/deliveries/access-2.21.md`.
2.22 agrega persistencia/consumo interno con concurrencia y rollback, migración
0009 y [contrato](docs/mfa-persistence-contract.md); login MFA aún no activo.
2.23 agrega auditoría global de consumos en el mismo commit (0010), sin
UPDATE/DELETE/TRUNCATE para la app. 2.24 incorpora cuotas MFA (0011), separadas
de contraseña y compartidas entre propósitos, con fallos confirmados y sin reset
al acertar un factor. 2.25 añade estado/revisión global (0012) con
revocación/auditoría atómicas como servicio interno. 2.26 añade pendientes
MFA tipados (0013). 2.27 compone pendiente/cuota/TOTP y guarda la prueba (0014).
2.28 consume la prueba al emitir sesión y exige revisión/evidencia al resolver (0015).
Cookies previas sin revisión dejan de servir, sin backfill. 2.29 inicia login
MFA interno con política global y pendientes restringidos. 2.30 añade desafíos
de buzón y permisos restringidos (0016), sin envíos. Sigue integrar entrega
de correo cifrada/cuotas e incorporación completa. Faltan los caminos completos de autenticación/
recuperación antes de HTTP/UI.
El contrato 2.20 ya define incorporación con prueba fresca de buzón, obligatoriedad
admin global, recuperación sin bypass y cambios futuros de sesión/confirmación.
2.19 incorpora escaneos obligatorios de secretos/dependencias en CI; el hallazgo
dev-only SEC-2026-001 permanece visible con vencimiento 2026-10-31. Detalles en
`docs/security-scanning.md`. La prioridad conserva el orden de seguridad
de la revisión del Plan Maestro v1.1. La API de confirmación verifica la
contraseña y rota cookies de sesión/CSRF tras commit, con contexto obligatorio,
no-store y slots compartidos. JSON ambiguo se rechaza en el middleware común.
La prueba interna dura cinco minutos y no extiende la vida absoluta. Migración
0008 sin backfill de confirmación; 0009/0010 agregan tablas MFA y de evidencia
vacías; 0011 agrega cuotas MFA sin cambiar el login actual. La guardia ya se
exige en alta/cambio, incluyendo
no-op; el alta vuelve a comprobarla antes del commit. La pantalla requiere
contraseña actual, relee contexto tras rotar y retira controles al caducar o perder
contexto. Web Locks coordina peticiones entre pestañas compatibles; no equivale
a MFA ni elimina resultados inciertos por desconexión. Por prioridad explícita
del usuario, seguridad precede al inventario: cerrar la puerta descrita en
`docs/security-review.md` antes de 3.1. No hay fecha prometida ni certificación ASVS.
VPS previsto: Contabo, compartido con imágenes. El usuario informa haber adquirido
taller-carrillo.com y apuntado el DNS al VPS; falta verificarlo desde aquí.
Preparar staging aislado/privado y HTTPS tras inspección del servidor,
sin usar la base operativa ni publicar con los riesgos de acceso abiertos.
El usuario autoriza limpiar la VPS si hace falta; identificar recursos y
respaldar datos antes, según `docs/staging-plan.md`. No hubo cambios remotos.
CI remota sigue
pendiente hasta conectar un repositorio autorizado.
