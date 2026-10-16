# Entrega: contrato HTTP de membresías existentes (acceso 2.10)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-membership-http`.

## Resultado

GET/PATCH `/api/auth/memberships/{membership_id}` conectan el servicio 2.9 con
HTTP. Solo sesión vigente y rol admin actual de la empresa seleccionada; el
servidor comprueba todo dentro de la transacción. GET devuelve ID, rol y estado,
sin correo, cuenta global, tenant elegido, tokens ni hashes. PATCH admite solo
rol/estado explícitos y aplica origen/CSRF del middleware existente. Query params
prohibidos. Sin altas, listado ni pantalla en esta entrega.

El contrato completo está en `docs/membership-http-contract.md`. Los nombres de
rutas, cabeceras y errores son decisiones técnicas, sin reglas comerciales nuevas.
No hay cambios de esquema, dependencias, frontend, secretos ni Nginx/Compose.

GET entrega ETag de representación estable; PATCH requiere un único If-Match
válido. Bajo los locks del servicio, después de sesión/permiso, se compara el
estado revisado con el actual: 412 si difiere, sin cambio/revocación/auditoría.
Sin cabecera: 428; formato no admitido/duplicado: 400. El tag no autoriza ni prueba
que el cliente leyó, y no es una revisión histórica monotónica: el mismo estado
restituido produce el mismo tag. No hay promesa de idempotencia general.

Identidad inválida produce 401; falta de permiso u objetivo ajeno/inexistente
comparten 403. Último administrador protegido con 409. Errores se traducen en un
punto común sin devolver inputs ni detalles DB. Respuestas no-store; las rutas
reutilizan los límites y logs saneados de /api/auth/ existentes. Si el cambio real
revoca al actor, la respuesta exitosa borra sus cuatro cookies solo después de
commit. Un no-op propio conserva cookies. Fallo de commit conserva cookies y
devuelve 503; no expone vista ni ETag de un cambio no confirmado.

## Archivos completos creados o modificados

- `backend/app/modules/access/administration_routes.py`: GET/PATCH, selección de
  cookie privada, prohibición de query, If-Match estricto, representación y cierre
  de cookies posterior al commit cuando se revoca el propio acceso.
- `backend/app/modules/access/administration.py`: vista/ETag, consulta autorizada,
  autorización compartida, distinción de identidad/permiso y precondición dentro
  de la transacción; resultado interno de revocación del actor.
- `backend/app/main.py`: inicializa servicio, registra rutas y traducción común de
  errores de acceso/admin/precondición/último admin/indisponibilidad.
- `backend/tests/integration/test_membership_routes.py`: **60 casos nuevos** de
  peticiones directas con PostgreSQL real, incluyendo concurrencia del servicio.
- `backend/tests/integration/proxy_access.py`: cliente de test admite PATCH y
  **un caso nuevo** por proxy real de consulta/cambio/412/aislamiento/log canario.
- `backend/tests/integration/conftest.py`: limpieza owner de auditoría sintética
  antes de retirar las identidades efímeras. No ejecuta limpieza en desarrollo.
- `backend/tests/test_membership_change.py`: **tres casos nuevos** de validador de
  representación y cambios de cada atributo público, sin campos de identidad privada.
- `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/membership-http-contract.md`, `docs/permissions.md`, `docs/decisions.md`,
  `docs/data-model.md` y este registro: contratos, resultados y límites actuales.

## Comandos exactos ejecutados

Desde la raíz:

```bash
.venv/bin/python -m ruff format --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts --fix
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_membership_change.py
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/integration/test_membership_routes.py --collect-only -q
python3 -m scripts.ci > /tmp/carrillo-access-2.10-ci.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.10-final-ci.log 2>&1
git diff --check
```

Para repetir la validación basta `python3 -m scripts.ci`. Crea su propio proyecto,
secretos temporales y DB tmpfs carrillo_test; valida aislamiento antes de probar.
Migra/revierte solo en esa DB y elimina sus recursos al finalizar, incluso ante
fallos. La colección local solo enumeró casos; no se presume ejecución PostgreSQL
por ese comando. Los 60 casos sí se ejecutaron en el pipeline aislado.

Actualización de desarrollo existente, sin ejecutar migraciones ni sembrar datos:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

Comprobación anónima adicional realmente ejecutada, sin cookies o cuenta real:

```bash
python3 - <<'PY'
from scripts.smoke_web import read
import json
body, headers = read('http://localhost:8080', '/api/auth/memberships/00000000-0000-4000-8000-000000000000', expected=401, headers={'X-Requested-With': 'XMLHttpRequest', 'Sec-Fetch-Site': 'same-origin'})
assert json.loads(body) == {'detail': 'Access denied'}
assert headers.get('Cache-Control') == 'no-store'
assert headers.get('ETag') is None
assert headers.get('Set-Cookie') is None
print('Admin route OK: anonymous 401, no-store, no identity, no ETag or cookies.')
PY
```

## Resultados reales

- Final **carrillo-ci-96e27c1e3add**, exit 0, **CI passed**.
- **420 backend** en 25.80s: **169 unitarias + 251 PostgreSQL**. Más **5 por proxy
  real** en 0.65s: **425 backend**. No duplicar las cinco pruebas separadas.
- **51 frontend** en 870ms y **13 scripts/configuración**: **489 pruebas** del
  pipeline, **64 nuevas** respecto a 2.9 (3 unitarias, 60 HTTP/DB y 1 por proxy).
- Ruff/formato correctos en 59 archivos Python; pip check, actionlint, ESLint,
  Prettier y tipos aprobados. Imágenes construidas con dependencias fijadas.
  Build Next.js reutilizó la capa ya validada: sin nueva compilación frontend sin
  caché ni cambios de interfaz. Migraciones upgrade/check/downgrade/upgrade siguen
  aprobadas en la DB efímera con revisión `0006_membership_audit`.
- Peticiones directas rechazan todos los perfiles no admin, auto-elevación,
  empresa ajena/objetivo desconocido, estados de sesión inválidos y pendientes sin
  sesión, origen/CSRF inválidos, campos extra, booleanos no estrictos, query,
  UUID inválido y precondición ausente/débil/lista/duplicada. Sin datos/ETag de
  objetivos rechazados. No hay DELETE ni permiso CORS en preflight.
- Cambio/no-op, último admin, auto-retiro y replay de sesión comprobados. Errores
  de commit de lectura, cambio y auto-retiro no publican éxito/vista/ETag ni borran
  cookies; rollback y ausencia de evento parcial verificados.
- Dos cambios distintos con el mismo tag: segunda conexión realmente espera en
  pg_locks y, tras el commit de la primera, rechaza el tag anterior. Un evento
  confirmado, sin sobrescribir el estado nuevo. No se usaron sleeps como prueba
  de solapamiento; el sondeo de locks tiene plazo acotado.
- Nginx/Uvicorn/PostgreSQL reales: GET/PATCH, 412 y 403 ajeno observados. El canario
  de query/header de administración no apareció en los logs capturados, junto
  a las comprobaciones anteriores de IP, selección, logout/replay y límite 429.
- Primer test unitario: 16 passed, 1 failed; `model_copy` del fixture omitía
  validación e introducía un str donde debía conservar un enum, activando warning
  tratado como error. Se corrigió a MembershipRole.MECHANIC. Run local posterior:
  **17 passed en 0.14s**, sin desactivar warnings ni reglas.
- Primer pipeline **e99073775c27** construyó la copia anterior de ese test y terminó
  **419 passed, 1 failed**, exit 1; sus 60 casos HTTP ya aprobaron. La ejecución
  final reconstruyó los tests corregidos y aprobó todo. No contar el run fallido
  ni la colección como una validación completa aprobada.
- Desarrollo: backend actualizado/recreado, Nginx recargado, **cuatro servicios
  saludables**. Smoke exit 0: HTML español, diez assets, API mismo origen, 404 y
  protecciones 403/413. Ruta administrativa anónima: 401/no-store, sin ETag/cookies.
- Sin migración nueva, cuentas reales nuevas, cambios de membresía en desarrollo,
  regeneración de secretos, publicación ni push. No hay prueba visual administrativa
  nueva: todavía no existe esa pantalla. La interfaz de acceso de 2.8 sigue servida
  en http://localhost:8080 y su suite se ejecutó otra vez en CI.

## Riesgos y siguiente tarea

Hito 2 abierto. Falta listado/pantalla, altas, gestión global de cuenta/contraseña,
permisos de módulos, consulta/ampliación de auditoría, recuperación y limpieza.
La autorización/último admin siguen dependiendo de servicios coordinados; SQL
arbitrario no queda protegido. Sin RLS ni auditoría a prueba del owner DB.

El tag valida estado actual, no historia ni una revisión obligatoria por un humano.
Una respuesta perdida puede ocurrir tras commit; no retry automático ni promesa
de rollback por timeout. Un actor que perdió acceso necesitará iniciar sesión de
nuevo o recurrir a otro administrador. La futura UI debe retirar cachés/contexto
al cambiar privilegios y renovar CSRF antes de escribir. No se probó una UI que
aún no existe, TLS público ni capacidad de carga.

Siguiente tarea **2.11**: listado paginado y acotado de membresías de la empresa
autorizada, con permiso admin y aislamiento probado, para preparar la pantalla
de administración. Producción/backups/HTTPS y CI remota mantienen sus pendientes.

Referencias de protocolo: [If-Match RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-match),
[428 RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html#section-3),
[errores FastAPI](https://fastapi.tiangolo.com/tutorial/handling-errors/).
