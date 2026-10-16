# Entrega: autenticación y límites compartidos (acceso 2.4)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-authentication`.

## Resultado

Servicio interno Authenticator: credenciales estrictas, Argon2id bajo bloqueo del
usuario, comprobación de membresía activa y sesión opaca devuelta tras commit.
Los rechazos ordinarios comparten el resultado None. La transacción del servicio
confirma los fallos para evitar que devolver un error HTTP pierda los contadores.
Infraestructura fallida genera AuthenticationUnavailable saneado, sin un token.

Se mantienen los límites del diseño: cinco fallos por cuenta y treinta por IP
en 15 minutos. Ventana fija iniciada por el primer fallo; al alcanzar el límite
se rechazan los intentos siguientes hasta vencer la ventana. Los intentos bloqueados
no extienden el plazo. Un éxito reinicia la cuenta y conserva los fallos de IP.
No se añade un bloqueo permanente al usuario.

Nueva tabla login_throttles: clave global scope/key_hash, fecha UTC de ventana y
fallos acotados por CHECK. Sin correo ni IP en claro; SHA-256 determinista no es
anonimización. IPv4 mapeada a IPv6 y variantes equivalentes comparten contador.
El servicio exige IP parseada procedente de un adaptador de red confiable.

Bloqueos en orden IP -> cuenta -> usuario -> sesiones. Upsert y lectura bloqueada
coordinan workers independientes. Una IP ya limitada no crea nuevos contadores de
cuenta ni ejecuta Argon2. Correo desconocido/hash inválido usa verificación Argon2id
de descarte, con hash aleatorio por instancia; no se promete tiempo constante.
Rehash de un perfil Argon2id anterior y nueva sesión se confirman juntos.

Contrato de seguridad HTTP previo en docs/auth-http-contract.md: entradas,
respuestas genéricas, cookies, origen, CSRF firmado y pruebas necesarias. Las rutas,
selección de empresa y handlers no se habilitaron en esta entrega. No se inventan
endpoints públicos para exponer un servicio aún sin las protecciones de transporte.

## Archivos cambiados

- `backend/app/modules/access/authentication.py`: contrato de credenciales,
  contadores y servicio transaccional de autenticación.
- `backend/app/modules/access/models.py`: LoginThrottle y restricciones.
- `backend/migrations/versions/0004_login_throttles.py`: migración explícita reversible.
- `backend/tests/test_credentials.py`: 13 casos de entrada, autoridad, IP y ventana.
- `backend/tests/integration/test_authentication.py`: 20 casos PostgreSQL de
  credenciales reales, límites compartidos, errores, rehash y concurrencia.
- `backend/tests/integration/test_postgres.py`: revisión esperada actualizada,
  manteniendo el ciclo completo de migraciones en DB efímera.
- `README.md`, `docs/decisions.md`, `docs/data-model.md`, `docs/acceptance.md`,
  `docs/auth-http-contract.md` y este registro.

Sin nuevas dependencias, cambios de frontend ni secretos de aplicación.

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

Para un entorno nuevo, primero generar secretos e iniciar DB siguiendo README.
Las pruebas con escritura y downgrade se ejecutan en PostgreSQL efímero, no en
la base de desarrollo. No se crearon cuentas ni sesiones reales locales.

## Resultados reales

- Pipeline local `carrillo-ci-a99e46bce448`: **exit 0, CI passed**.
- **149 pruebas backend aprobadas en 4.95s**, incluidas **76 con PostgreSQL real**.
- **15 frontend** y **6 del ejecutor** aprobadas: **170 pruebas en el pipeline**.
- Unitarias locales: **73 aprobadas en 0.51s**, 76 integración excluidas por ese
  comando. Se repiten casos del pipeline, no se suman como pruebas distintas.
- Ruff/formato correctos en **37 archivos**; pip check, actionlint, ESLint,
  Prettier y tipos correctos. Builds completados con caché donde correspondía.
- Migración y roundtrip en DB efímera correctos; metadata sin deriva.
- Desarrollo: **0004_login_throttles (head)**; Alembic check **No new upgrade
  operations detected**. Cuatro servicios saludables tras actualizar imagen.
- Smoke CI y local: HTML español, nueve assets, API mismo origen, 404 y cabeceras
  correctos. La pantalla inicial sigue indicando que el acceso está pendiente.
- Se verificaron acceso correcto y rechazos por contraseña, correo desconocido,
  usuario/empresa/membresía inactivos, empresa ajena y hash almacenado inválido.
  Todos los rechazos persistieron contadores y no crearon sesiones.
- Límites comprobados entre IP e instancias distintas; treinta correos distintos
  agotaron el bucket de IP y el intento siguiente no creó otra fila de cuenta.
- Éxito reinició la cuenta conservando fallos de IP; ventana vencida permitió
  autenticar. El límite inclusivo exacto de 15 minutos se prueba con reloj
  determinista en una unitaria; expiración en DB usa clock_timestamp real.
- Concurrencia: tras cuatro fallos, dos instancias/IP intentaron simultáneamente;
  se observó espera en pg_locks, solo una ejecutó verificación y el contador final
  fue cinco. Cambio de contraseña con bloqueo impidió verificar un hash antiguo.
- Perfil Argon2id anterior actualizado en login correcto. Fallos inyectados en
  insert/commit no devolvieron token ni dejaron sesión o contadores parciales;
  excepción saneada no incluyó el secreto canario y el reintento funcionó.
- CHECK de rangos rechazó -1/6 en cuenta y 31 en IP. Campos extra de autoridad
  y contraseñas fuera de límites/Unicode inválido rechazados por el contrato.
- El ejecutor retiró recursos CI y sus secretos temporales; desarrollo conservó
  volumen y credenciales. Sin push ni despliegue público.

## Riesgos y límites

Este servicio no es todavía login web. El adaptador HTTP, cookies/CSRF, origen,
IP de proxy confiable, selección multiempresa y formulario siguen pendientes.
El parámetro selected_tenant_id es interno y solo propone una membresía; no se
puede convertir en autoridad del cliente ni exponer emisión por UUID de usuario.
La API aún no instancia ni conecta Authenticator a peticiones públicas.

La serialización por IP limita paralelismo de redes compartidas y retiene conexión
mientras verifica Argon2. Antes de habilitar HTTP hacen falta límites de volumen,
tamaño y espera en proxy/API y medición de memoria/CPU en el host destino.
No se ensayó carga de producción ni se promete resistencia completa a DoS.

No hay limpieza automática de contadores; se añadió índice temporal. Definir
retención y mantenimiento antes de exposición para controlar crecimiento. Los
hashes de correo/IP pueden inferirse por enumeración y siguen siendo datos a
proteger. No hay RLS y un operador con DML puede alterar el estado manualmente.

Los cinco fallos pueden impedir temporalmente entrar con contraseña correcta:
es la política adoptada, con ventana finita y sin bloqueo permanente. Credenciales
comprometidas, recuperación, auditoría y último administrador siguen pendientes.
El rehash automático conserva la contraseña y no revoca otras sesiones; un cambio
real de contraseña debe revocarlas mediante su servicio transaccional.

No se implementaron todavía los requisitos del contrato HTTP ni su flujo de
preautenticación/selección de empresa; las pruebas de CSRF/proxy son criterios
futuros, no resultados ejecutados. GitHub Actions remoto sigue pendiente: no hay
remoto configurado.

## Siguiente tarea

Acceso 2.5: protección de transporte con cookies, origen y CSRF firmado. Cerrar
y probar el flujo de selección de empresa antes de conectar autenticación a HTTP;
después implementar el formulario y el recorrido a través del proxy.
