# Entrega: cookies, origen y CSRF firmado (acceso 2.5)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-http-security`.

## Resultado

Middleware ASGI conectado a la API: peticiones con efectos exigen Origin exacto,
prueba CSRF firmada en cabecera y cookie, y binding a sesión o nonce previo al
login. Rechaza pruebas manipuladas, caducadas, ajenas o ambiguas antes del handler.
Sec-Fetch-Site, cuando existe, debe ser same-origin. GET/HEAD/OPTIONS conservan su
semántica. No se agregaron rutas públicas de acceso o emisión de pruebas.

HMAC-SHA-256 con codificación estructurada y propósitos separados, nonces aleatorios
256 bits y vigencia de 15 minutos. La prueba nunca contiene el token de sesión.
Cookie preauth también firmado; no acredita identidad ni contraseña verificada.
Comparaciones de firma/cookie-cabecera en tiempo constante sobre formato acotado.

Helpers de cookies: sesión/preauth HttpOnly, CSRF legible por JS; Path=/, sin Domain,
SameSite=Lax, Secure y prefijo __Host- en HTTPS. HTTP solo para loopback en
entornos development/test. Emitir sesión retira preauth y reemplaza la prueba;
renovar CSRF no modifica expires_at de sesión. Borrado conserva atributos de scope.

El servidor HTTP exige origen canónico y clave externa de 32 bytes hex, sin reserva.
Se añade la clave a entornos existentes sin regenerar DB; se monta en backend/tests.
CLI/migraciones no requieren la configuración HTTP para operar.

JSON UTF-8 hasta 8192 bytes, incluido cuerpo fragmentado, y límite equivalente en
Nginx /api/. Rechazos 403/415/413/400 saneados y no-store; errores Pydantic 422 sin
inputs. Los handlers futuros siguen necesitando sesión viva y permisos en servidor.
CSRF válido **no equivale a autenticación ni autorización**.

## Archivos cambiados

- `backend/app/modules/access/http_security.py`: origen, HMAC, cookie policy y guard.
- `backend/app/modules/access/http_middleware.py`: comprobaciones ASGI, tamaño,
  lectura/reproducción de cuerpo, JSON y errores sin datos sensibles.
- `backend/app/core/config.py`: origen y clave externa/archivo, secretos saneados.
- `backend/app/main.py`: conecta middleware y handler de validación saneado.
- `backend/tests/test_http_security.py`: 41 casos con rutas exclusivas de test.
- `backend/tests/test_config.py`, `backend/tests/test_app.py`,
  `backend/tests/conftest.py`: clave/configuración y expectativas de protección.
- `compose.yaml`, `.env.example`: origen y montaje del secreto, sin valor real.
- `scripts/setup_local.py`: nueva clave aleatoria al crear entornos.
- `scripts/setup_http.py`: upgrade local idempotente que preserva credenciales DB.
- `scripts/tests/test_setup_http.py`: tres pruebas de generación/upgrade.
- `scripts/ci.py`, `scripts/tests/test_ci.py`: origen real del puerto efímero,
  recarga de Nginx conservando el puerto y dos regresiones nuevas.
- `ops/proxy/nginx.conf`: límite 8 KiB y 413 JSON/no-store en API.
- `scripts/smoke_web.py`: agrega rechazos 403 y 413 a través del proxy real.
- `README.md`, `docs/auth-http-contract.md`, `docs/decisions.md`,
  `docs/acceptance.md` y este registro.

Sin cambios de esquema, dependencias ni frontend. Revisión DB sigue 0004_login_throttles.

## Comandos exactos ejecutados

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
python3 -m unittest discover -s scripts/tests
python3 -m scripts.ci
python3 -m scripts.setup_http development
python3 -m scripts.setup_http test
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --force-recreate proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

Los comandos setup_http se usaron con los dos entornos locales ya existentes;
para instalación nueva setup_local.py genera también la configuración HTTP.
No repetir setup_local sobre un volumen inicializado. No imprimir el archivo de
clave ni copiar su contenido a Git, logs o variables NEXT_PUBLIC.

## Resultados reales

- Pipeline final `carrillo-ci-34f8318d2a41`: **exit 0, CI passed**.
- **197 pruebas backend aprobadas en 5.04s**: 121 unitarias y 76 PostgreSQL.
- **15 frontend** y **11 del ejecutor/configuración**: **223 pruebas en el pipeline**.
- Unitarias locales: **121 aprobadas en 0.68s**, 76 integración excluidas por ese
  comando. Son los mismos casos del pipeline, no un total adicional.
- Pruebas scripts locales: 11 aprobadas. Ruff y formato correctos en 42 archivos;
  pip check, actionlint, ESLint, Prettier y tipos correctos. Builds con caché donde
  correspondía; no se presume una construcción sin caché.
- Origen/CSRF válidos permiten llegar al handler de test. Origen ausente/null/ajeno,
  variantes no canónicas, Sec-Fetch-Site cruzado, falta de prueba, firma incorrecta,
  nonce diferente, sesión cambiada y valores duplicados no ejecutan ese handler.
- Expiración exacta de prueba y cambio de clave rechazan. Flags de cookies, scope,
  caducidades y borrado se verificaron en perfiles HTTPS/producción y HTTP local.
- Cuerpos inválidos/grandes y cuerpos ASGI fragmentados se rechazan. Error 422 no
  repite una contraseña canario. Cookie no ASCII se rechaza sin excepción sensible.
- Upgrade local/test y sus unitarias preservan credenciales existentes; no se
  crearon usuarios/sesiones reales ni se reinicializó el volumen PostgreSQL.
- Desarrollo: cuatro servicios saludables y Alembic check **No new upgrade
  operations detected**. Configuración aplicada a backend y proxy.
- Smoke CI y local correctos: HTML español, nueve assets, API mismo origen, 404,
  cabeceras, 403 de guard y 413 JSON/no-store de Nginx. La pantalla sigue sin login.
- CI ajustó PUBLIC_ORIGIN al puerto publicado, recreó backend y recargó Nginx sin
  reiniciar el proxy; verificó que el puerto no cambió antes del smoke.
- Primera ejecución `carrillo-ci-9164f6987a62`: **exit 1** en smoke con conexión
  rechazada. Reiniciar el proxy había reasignado su puerto efímero. Se corrigió
  usando recarga Nginx y se añadieron regresiones. No se cuenta ese intento como
  pipeline aprobado. El segundo run completo sí pasó y limpió sus recursos.
- El runner retiró contenedores/redes y ejecutó su limpieza de imágenes/carpetas
  temporales. Sin publicación, push ni despliegue público.

## Riesgos y pendientes

No hay login/logout HTTP ni un endpoint público que emita/renueve CSRF. La política
se prueba con rutas exclusivas de tests, ausentes de la imagen runtime. La única
ruta pública actual sigue siendo liveness. Falta cerrar selección multiempresa,
IP confiable del proxy y formulario antes de habilitar el acceso.

La prueba CSRF ligada a una sesión no consulta DB: cada futuro handler debe
comprobar sesión viva/revocación y permisos. Un nonce preauth firmado solo protege
intención, no es un estado de usuario autenticado. La selección de empresa requiere
un estado provisional de identidad separado. Los tokens/pruebas reutilizables no
proporcionan idempotencia de negocio. El helper de cookie se llama tras commit;
esta fase no implementa aún ese flujo HTTP transaccional.

El smoke por proxy comprueba rechazos y tamaño; no demuestra todavía login,
revocación HTTP ni defensa contra IP de cabeceras falsificadas. Sigue pendiente
la prueba de IP de extremo a extremo y límites de volumen/espera antes de exponer.

La política actual de escrituras es JSON de 8 KiB. Adjuntos futuros necesitarán
parser y límite explícitos con las mismas protecciones de origen/CSRF. No habilitar
multipart quitando globalmente el guard. La clave debe ser común a todos los
workers; rotación coordinada invalida pruebas anteriores. Los relojes deben estar
sincronizados. CSRF no protege contra XSS; hardening de producción sigue pendiente.

PUBLIC_ORIGIN es exacto: localhost y 127.0.0.1 son orígenes distintos. HTTP sin
Secure se limita a loopback development/test. No se desplegó HTTPS real ni se
presume cumplimiento de producción por probar flags en TestClient.

El hito 2 sigue abierto: administración de permisos, último administrador, auditoría,
recuperación y limpieza de contadores/sesiones también están pendientes. GitHub
Actions remoto sigue sin ejecutarse porque no hay remoto configurado.

## Siguiente tarea

Acceso 2.6: cerrar e implementar login/logout HTTP y selección de empresa desde
identidad verificada, con IP confiable del proxy y las protecciones ya probadas.
Después conectar el formulario y validar el recorrido real a través del proxy.
