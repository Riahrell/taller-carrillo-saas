# Entrega: acceso HTTP y proxy confiable (acceso 2.7)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-http-login`.

## Resultado

Login/logout HTTP, consulta de sesión, emisión/renovación CSRF y selector de
empresa conectados a los servicios verificados. Solo cookies HttpOnly transportan
identidad; los tokens no aparecen en JSON. Contexto de usuario/empresa/rol desde
DB y rechazo de campos extra. Cero empresas rechaza, una emite sesión, varias
requieren confirmar una membresía propia desde identidad provisional verificada.

Consumo y sesión confirman antes de emitir cookies. Logout revoca sesión y acceso
pendiente ligado al navegador antes de borrarlas, también con cookies mixtas;
fallo de commit conserva cookies y revierte ambos cambios para reintentar.
La renovación CSRF conserva un nonce vivo durante selección, y no extiende la
expiración absoluta de una sesión. Un acceso nuevo requiere cerrar sesión primero.

Red api interna dedicada a Nginx/backend; frontend permanece en edge y DB en
private. Uvicorn conserva socket peer real con --no-proxy-headers. API acepta
X-Real-IP solo desde el proxy configurado y resuelto en DNS Docker; Nginx
sobrescribe ese header y elimina cabeceras de forwarding externas. Probado que
IPs falsificadas no cambian el bucket y que un peer directo no puede hacerse proxy.

Dos verificaciones de contraseña por worker, límites generales del proxy y de
conexiones Uvicorn, espera local de locks/sentencias y errores saneados/no-store.
Nginx no registra queries/cuerpos/cookies en access log; descarta errores de
request de acceso porque pueden incluir queries. CI comprueba un canario
sintético también al provocar 429. El contrato detalla alcance y límites.

Sin migración, dependencias ni frontend nuevos. Revisión DB 0005_login_challenges.
El formulario y el resto del hito 2 siguen pendientes; esta entrega cierra el
adaptador HTTP, no habilita operaciones comerciales ni producción.

## Archivos completos cambiados

- `backend/app/modules/access/routes.py`: cinco rutas y contratos de entrada.
- `backend/app/modules/access/http_service.py`: consulta de sesión y logout
  transaccionales, contexto de servidor y límite de hashing simultáneo.
- `backend/app/modules/access/trusted_proxy.py`: validación de socket peer, DNS
  y única IP observada por Nginx; errores saneados y sin reserva.
- `backend/app/modules/access/http_security.py`: cookie provisional, lecturas de
  navegador protegidas, parsing único de cookies, binding firmado y renovación.
- `backend/app/modules/access/authentication.py`: límites locales de transacción.
- `backend/app/main.py`: inicialización por worker, router y errores 403/503.
- `backend/app/core/config.py`: TRUSTED_PROXY_HOST validado; obligatorio en HTTP.
- `backend/Dockerfile`: proxy headers desactivados, sin access log y concurrencia.
- `compose.yaml`, `compose.ci.yaml`: red api, identidad DNS y acceso del test aislado.
- `ops/proxy/nginx.conf`: forwarding saneado, buffering/límites, errores JSON,
  no-store y logs de acceso sin datos sensibles de request.
- `backend/tests/integration/test_access_routes.py`: 38 casos HTTP con PostgreSQL,
  incluido rollback y cierre atómico de sesión/pendiente con cookies mixtas.
- `backend/tests/integration/proxy_access.py`: cuatro casos explícitos de CI por
  Nginx/Uvicorn reales; excluido por nombre de la colección automática normal.
- `backend/tests/integration/conftest.py`: identidades aleatorias sintéticas y
  limpieza dentro del guard de base efímera ya existente.
- `backend/tests/test_trusted_proxy.py`: 18 casos de confianza y configuración.
- `backend/tests/test_http_security.py`: dos casos nuevos de flags de cookie
  provisional en HTTPS/producción y HTTP/loopback; conserva pruebas anteriores.
- `backend/tests/conftest.py`: aislamiento de la nueva configuración.
- `scripts/ci.py`: valida redes, ejecuta tests por proxy tras fijar origen/puerto
  y examina logs capturados sin imprimirlos.
- `scripts/tests/test_ci.py`: aislamiento de variables y dos regresiones de logs.
- `.env.example`, `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/data-model.md`, `docs/decisions.md` y este registro: contrato y operación.

## Comandos exactos ejecutados

Desde la raíz del repositorio:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
timeout 60 .venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
.venv/bin/python -m uvicorn --help
python3 -m scripts.ci
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps --force-recreate proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -t
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
git diff --check
```

El runner ejecuta, dentro de su proyecto efímero y después de iniciar el proxy y
configurar su origen real: `run --rm -e RUN_PROXY_INTEGRATION=1 tests python -m
pytest -p no:cacheprovider tests/integration/proxy_access.py`. No ejecutar ese
archivo contra desarrollo. El fixture exige base carrillo_test, roles separados,
APP_ENV=test y esquema permitido, además de la activación explícita del runner.

Verificación local adicional de CSRF/logout anónimo, sin crear cuentas ni mostrar
tokens (comando exacto ejecutado):

```bash
python3 - <<'PY'
import json
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
base = 'http://localhost:8080'
jar = CookieJar()
opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
def call(path, headers, data=None):
    try:
        response = opener.open(Request(base + path, headers=headers, data=data), timeout=10)
    except HTTPError as error:
        response = error
    with response:
        assert response.headers.get('Cache-Control') == 'no-store'
        response.read(8192)
        return response.status
read = {'X-Requested-With': 'XMLHttpRequest', 'Sec-Fetch-Site': 'same-origin'}
assert call('/api/auth/csrf', {}) == 403
assert call('/api/auth/csrf', read) == 204
assert call('/api/auth/session', read) == 401
proof = next(cookie.value for cookie in jar if cookie.name == 'carrillo_csrf')
assert call('/api/auth/logout', {'Origin': base, 'Content-Type': 'application/json', 'X-CSRF-Token': proof}, b'{}') == 204
assert not list(jar)
print('Acceso anónimo OK: CSRF 403/204, sesión 401, logout 204 y cookies retiradas; sin crear cuentas.')
PY
```

## Resultados reales

- Pipeline final **carrillo-ci-9e9b84be3659**, **exit 0, CI passed**.
- Colección backend habitual: **307 aprobadas en 15.95s**: 152 unitarias y
  155 PostgreSQL. Suite adicional por proxy real: **4 aprobadas en 0.54s**.
  Total backend: **311**; no contar las pruebas por proxy dentro de las 307.
- Frontend: **15 aprobadas**. Herramientas/configuración: **13 aprobadas**.
  **339 pruebas en el pipeline**, 64 más que en acceso 2.6.
- Unitarias locales finales: **152 aprobadas en 2.18s**, 155 integración excluidas.
  Son los mismos casos del pipeline, no se suman otra vez.
- Ruff/formato correctos en 52 archivos; pip check, actionlint, ESLint, Prettier,
  tipos y builds ejecutados correctamente. Se usó caché de build donde correspondía.
- TestClient y PostgreSQL: sesión única/multiempresa, nonce conservado al renovar,
  rol/contexto actuales, selección ajena/otro navegador, replay tras logout,
  estados revocados/caducados/inactivos y campos de autoridad rechazados.
- Login sin origen/CSRF y lecturas de otros orígenes rechazados sin cookies nuevas.
  Error de validación no refleja contraseña/campos extra; el 401 no distingue
  causas ni incluye Retry-After de cuenta. Saturación de hashing devuelve 503.
- Fallos de commit de login, selección, consulta, CSRF y logout: sin cookies de
  autenticación publicadas/retiradas ni efectos parciales. Cierre con cookies
  mixtas comprobado tanto con commit como con rollback y reintento.
- Nginx real: dos logins fallidos con distintas cabeceras falsas usan un solo
  bucket cuyo hash coincide con la IP de socket del test en api. Se comprueban
  login correcto, contexto, logout y rechazo de cookie anterior; también selección
  multiempresa y rechazo de login directo al backend con forwarding falsificado.
- Once cargas JSON incompletas, acotadas, provocan **429 JSON/no-store** sin crear
  contadores de contraseña; las conexiones se cierran al terminar la prueba.
- El canario sintético no aparece en logs capturados de Nginx/API, incluido el
  rechazo 429 con query. No se imprimen esos logs durante la comprobación.
- Migraciones upgrade/check/downgrade/upgrade siguen aprobando en DB efímera.
  Desarrollo conserva 0005_login_challenges y Alembic check responde
  **No new upgrade operations detected**; no se aplicó una migración nueva.
- Desarrollo: backend y proxy actualizados, nueva red interna api, cuatro servicios
  saludables. nginx -t correcto, smoke HTML/API correcto y recorrido local anónimo
  CSRF 403/204 -> sesión 401 -> logout 204 sin cookies restantes. Sin cuentas reales.
- La pantalla sigue mostrando que el acceso de interfaz no está disponible;
  el login HTTP ya existe, el formulario aún no está conectado.
- Runs intermedios 7aa2c7df1dfd y 10e128ed6186 fallaron en lint por una variable
  sin usar y una línea larga de test respectivamente. Se corrigieron y no se
  cuentan como aprobados. Runs intermedios aprobados tampoco se suman al total;
  la evidencia final corresponde al proyecto citado arriba y al código actual.
- El runner limpió contenedores/redes y sus recursos temporales al terminar.
  Sin push, publicación, usuarios reales creados ni credenciales regeneradas.

## Riesgos y pendientes

El formulario, navegación de sesión, selector visible, expiración y manejo de
errores en Next.js siguen pendientes. Las pruebas por proxy son HTTP real desde
un cliente Python, no una prueba de navegador. Los flags HTTPS se prueban en la
política de cookies; no se desplegó TLS público ni se afirma preparación productiva.

La confianza DNS/red supone control del daemon Docker y del servicio proxy por
el operador. No protege contra un administrador del host comprometido. Añadir
otro proxy/load balancer exige revisar la cadena; no usar IP de cabeceras externas
ni ampliar confianza a todo rango privado. No se verificó IPv6 real en Nginx.

El perfil de capacidad es inicial: medir Argon2, memoria y carga antes de producción.
Los límites locales de lock/sentencia no son un timeout total de petición/DNS/pool.
Los errores de request de acceso en Nginx se descartan para proteger datos; la
observabilidad actual conserva status y futuros eventos auditados deberán evitar
secretos. El canario demuestra los caminos probados, no todos los fallos posibles.

Los administradores futuros deben coordinar revocación de sesiones/pendientes
con cambios de contraseña/estado/privilegios. Faltan permisos por operación, último
administrador, auditoría, recuperación y mantenimiento de registros. No hay RLS
ni garantía contra SQL arbitrario con el rol de aplicación. Hito 2 abierto.
HTTPS, backups cifrados/restauración y hardening siguen pendientes. La política
fiscal continúa bloqueando cobro real; no se agregaron reglas comerciales.
CI remota sigue sin ejecutarse: no hay remoto Git configurado.

Referencias y decisiones técnicas en docs/auth-http-contract.md y docs/decisions.md.

## Siguiente tarea

Acceso 2.8: formulario Next.js de login, selector de empresa, consulta de sesión
y logout; errores/expiración, teclado/foco y recorrido real de navegador. Mantener
contraseñas/tokens fuera de almacenamiento JS y usar el contrato de cookies/CSRF.
