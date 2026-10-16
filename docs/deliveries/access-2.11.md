# Entrega: listado paginado de membresías (acceso 2.11)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-membership-list`.

## Resultado

GET `/api/auth/memberships` lista membresías existentes de la empresa autorizada,
con sesión vigente y perfil admin actual en cada página. Resuelve autoridad desde
cookie privada; no admite tenant, rol u orden del cliente. Devuelve únicamente
membership_id, email existente, role, is_active y user_is_active. Los estados de
cuenta/membresía inactivos se muestran sin habilitarlos. Sin hash/token, ID global
del usuario ni información de sus otras empresas.

Query opcional limit/after: 25 registros por defecto, máximo 100 y cursor UUID
como frontera exclusiva. Orden ascendente por UUID, usando índice existente
(tenant_id, id), LIMIT limit+1 y sin OFFSET ni COUNT global. next_after indica
último UUID si se observó otra fila; null al finalizar. Un cursor desconocido o
ajeno solo cambia la frontera, sin consultar si existe ni conceder acceso.
Parámetros desconocidos, repetidos o inválidos se rechazan con 422 sin eco.

La autorización compartida conserva empresa -> usuario actor -> sesión. El SELECT
de página no bloquea usuarios objetivo después de adquirir la sesión del actor.
El lock de empresa coordina cambios de membresías existentes; futuras operaciones
globales deben seguir el mismo protocolo. Respuesta solo después de commit,
no-store y sin cookies/ETag de colección. Error de commit no publica correos.
No hay cambios de membresía, auditoría ni revocaciones por consultar.

Las páginas leen estado actual; no son snapshot persistente ni exportación
consistente. Una inserción anterior al cursor puede requerir reiniciar la consulta.
Antes de editar se debe releer el detalle y usar su ETag del contrato 2.10.
Sin cambios de esquema, dependencias, frontend, secretos, Nginx o Compose.

## Archivos completos creados o modificados

- `backend/app/modules/access/administration.py`: query validada, DTO de lista y
  página, lectura acotada y extracción de autorización común conservando locks.
- `backend/app/modules/access/administration_routes.py`: GET de colección,
  query model Pydantic y rechazo de parámetros repetidos, sin relajar el contrato
  de detalle/PATCH que sigue prohibiendo query params.
- `backend/tests/test_membership_pagination.py`: **24 casos nuevos** de límites,
  formato canónico, cursor UUID y rechazo de campos extra de autoridad.
- `backend/tests/integration/test_membership_routes.py`: **32 casos nuevos** con
  PostgreSQL y peticiones directas. Fixture de 105 membresías propias, recorrido
  con 1/25/100 por página, estados inactivos, privacidad, identidad/rol, errores y
  lectura de un cambio entre páginas. El pendiente de login también rechaza listado.
- `backend/tests/integration/proxy_access.py`: amplía el caso de administración
  existente con dos páginas por Nginx/Uvicorn reales; no añade otro caso al conteo.
- `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/membership-http-contract.md`, `docs/permissions.md`, `docs/decisions.md`,
  `docs/data-model.md` y este registro: alcance actual, límites, evidencia y siguiente.

## Comandos exactos ejecutados

Desde la raíz:

```bash
.venv/bin/python -m ruff format --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_membership_pagination.py
python3 -m scripts.ci > /tmp/carrillo-access-2.11-ci.log 2>&1
git diff --check
```

Para repetir el conjunto completo basta `python3 -m scripts.ci`. Sus credenciales
y proyecto son temporales y la DB carrillo_test usa tmpfs y guards de aislamiento.
Pruebas y upgrade/check/downgrade/upgrade se ejecutan solo allí. El runner limpia
contenedores, redes, imágenes propias y directorio temporal al finalizar.

Actualización del desarrollo existente, sin migraciones ni carga de usuarios:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

Comprobación anónima adicional realmente ejecutada, sin identidad operativa:

```bash
python3 - <<'PY'
from scripts.smoke_web import read
import json
body, headers = read('http://localhost:8080', '/api/auth/memberships?limit=25', expected=401, headers={'X-Requested-With': 'XMLHttpRequest', 'Sec-Fetch-Site': 'same-origin'})
assert json.loads(body) == {'detail': 'Access denied'}
assert headers.get('Cache-Control') == 'no-store'
assert headers.get('ETag') is None
assert headers.get('Set-Cookie') is None
print('Membership list OK: anonymous 401, no-store, no personal data or cookies.')
PY
```

## Resultados reales

- Pipeline **carrillo-ci-e3f380d3fab1**, exit 0, **CI passed**.
- **476 backend** en 30.90s: **193 unitarias + 283 PostgreSQL**. Más **5 por proxy
  real** en 0.68s: **481 backend**. No duplicar esos cinco casos separados.
- **51 frontend** en 882ms y **13 scripts/configuración**: **545 pruebas** en total,
  **56 nuevas** respecto a 2.10 (24 unitarias + 32 PostgreSQL). La ampliación por
  proxy aporta cobertura adicional dentro de un caso existente, sin inflar conteo.
- Ruff/formato correctos en 60 archivos Python; pip check, actionlint, ESLint,
  Prettier y tipos aprobados. Imágenes construidas; el build Next.js reutilizó
  la capa ya validada, sin nueva compilación frontend sin caché. Migraciones
  siguen en `0006_membership_audit`, sin cambios de metadata.
- Unitarias de paginación: **24 passed en 0.12s**. El primer format --check tras
  añadir limpieza de fixture indicó un archivo por formatear; se formateó antes
  de que el build copiara los tests. El pipeline comprobó 60 archivos formateados
  y ejecutó la fuente corregida. No se cuenta el chequeo inicial como aprobado.
- 105 membresías propias recorridas con límites 1, 25 y 100 sin repeticiones ni
  omisiones en el conjunto estable. Default 25 correcto; última página y frontera
  posterior a todo registro devuelven next_after null. Se comprobaron los únicos
  cinco campos permitidos, cuentas/membresías inactivas y exclusión de IDs ajenos.
- Cursor ajeno/desconocido mantiene el filtro de empresa; no consulta pertenencia
  del cursor. Se rechazan tenant/role/offset, límites inválidos y parámetros
  duplicados. Perfiles cajero/mecánico/solo lectura y sesiones ausentes, revocadas,
  inactivas o caducadas no reciben datos. Lectura sin intención same-origin rechazada.
- Fallo inyectado de commit devuelve 503 saneado, sin correos/canario, cookies
  o ETag; no altera membresías, revocaciones ni auditoría. Cambiar un estado entre
  páginas se refleja al leer la siguiente; la limpieza de auditoría de ese fixture
  usa el owner exclusivamente en DB efímera y antes de retirar sus identidades.
- Dos páginas por proxy real cumplen no-store, tamaño, frontera y aislamiento.
  Todas las pruebas anteriores de permisos, ETag concurrente, último admin,
  rollback, IP, selección/logout y logs canario se volvieron a ejecutar y aprobaron.
- Desarrollo: backend actualizado/recreado, Nginx recargado y **cuatro servicios
  saludables**. Smoke exit 0: HTML español, diez assets, API mismo origen, 404 y
  rechazos 403/413. Listado anónimo observado: 401/no-store sin datos/cookies/ETag.
- No se crearon cuentas reales ni se cambió una membresía en desarrollo. No hay
  migración nueva, regeneración de secretos, publicación o push. No se hizo
  revisión visual nueva: la pantalla administrativa todavía no está implementada.

## Conexión y riesgos pendientes

El entorno actual usa servicios locales y assets servidos desde localhost:8080.
Un corte de internet no requiere detenerlos; necesitan que equipo y Docker sigan
funcionando. La conversación y descargas de herramientas/dependencias sí requieren
conectividad. No se simuló una caída de internet ni se agregó sincronización o un
modo offline para una instalación remota. Los archivos y commits viven en disco.

Hito 2 abierto: faltan pantalla, altas, operaciones globales de cuenta/contraseña,
permisos de módulos, consulta/ampliación de auditoría, recuperación y limpieza.
No hay RLS ni garantía frente a SQL arbitrario. El correo es dato personal:
conservar no-store, no registrar respuestas y retirar caché/contexto al perder
sesión/permiso. La futura UI no debe considerar el listado como autorización ni
reutilizarlo como estado revisado para escribir sin consultar el detalle.

Páginas vivas, sin total/snapshot entre requests; inserciones detrás del cursor
requieren reiniciar la consulta. El lock de empresa serializa administración y
debe medirse antes de aumentar carga. TLS público, capacidad, backups/restauración
y CI remota siguen pendientes; no se afirman validaciones de producción.

Siguiente tarea **2.12**: pantalla de membresías existentes con listado, revisión
del detalle y cambio de rol/estado, errores 401/403/409/412, renovación CSRF y
limpieza de contexto de sesión; pruebas de componente y recorrido de navegador
con identidades sintéticas aisladas antes de habilitarla.

Referencias: [query models FastAPI](https://fastapi.tiangolo.com/tutorial/query-param-models/),
[orden y LIMIT PostgreSQL](https://www.postgresql.org/docs/current/queries-limit.html).
