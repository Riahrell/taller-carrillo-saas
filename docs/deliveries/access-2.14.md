# Entrega: API de alta administrativa (acceso 2.14)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-user-provisioning-api`.

## Resultado

POST `/api/auth/memberships` conecta el servicio de alta probado en 2.13.
Exige sesión admin actual en empresa activa, origen/CSRF firmado y una única
X-Expected-Membership canónica obligatoria. Esta cabecera compara contexto con
el actor autenticado; no selecciona usuario, tenant ni autoridad. Sin query ni
campos extra: JSON email/password/role obligatorios. No hay registro público.

La respuesta 201 solo sale después de confirmar usuario nuevo, membresía y
auditoría en un commit. Contiene membership_id/role/is_active, ETag y cabecera
X-Access-Membership del actor. Location relativo `./memberships/{id}` resuelve
al detalle tanto por Nginx `/api/auth/` como en el adaptador directo `/auth/`,
sin confiar en Host para construir un origen absoluto. No devuelve contraseña,
hash, token, ID global ni correo; no emite cookies ni inicia sesión como el nuevo
usuario. La sesión del administrador se conserva.

Correo global existente: 409 User creation unavailable, sin adjuntar, reactivar,
resetear contraseña o revelar empresa/estado/ID. Repetir POST no recrea el alta
ni reemplaza credenciales; no se promete idempotencia general. Un resultado
perdido exige consultar listado/sesión, sin reintentar escrituras automáticamente.
Validación y fallos devuelven mensajes saneados/no-store, sin metadatos de éxito.
Fallo de commit devuelve 503 y revierte todos los registros de alta.

La ruta reutiliza la validación de 2.13, middleware de origen/CSRF/JSON hasta
8192 bytes y límites de conexiones/volumen del proxy de acceso. Comparte con
login los dos slots Argon2 por proceso, sin cola al saturar: 503 sin alta. No se
agregaron migraciones, dependencias, configuración o cambios de frontend.

Canal definido para el formulario siguiente: alta presencial/asistida en equipo
controlado por administrador, donde la persona nueva elige/escribe su contraseña
en campo oculto. Sin enviar claves por chat/correo/URL, generación predeterminada
o archivos. HTTPS fuera del desarrollo loopback. Requiere confianza en equipo y
administrador; no implementa invitación remota, verificación de correo, cambio
inicial forzoso o recuperación. El formulario de alta sigue pendiente.

## Recuperación tras reinicio del equipo

El trabajo en curso de 2.14 seguía en disco y el último commit previo era e352f3b.
Los servicios estaban detenidos. Se comprobó la existencia de
`carrillo-dev_postgres_data` y se levantaron las imágenes existentes con ese
volumen, sin regenerar secretos, ejecutar bootstrap, restaurar o recrear DB.
Consulta de solo lectura: revisión 0007_membership_creation_audit, **un usuario y
una membresía**. No se consultaron identidades, hashes ni contraseñas. La copia
privada previa a 2.13 seguía disponible; no hizo falta utilizarla.

Después de validar 2.14 se reconstruyó/recreó únicamente backend y se recargó
Nginx. No se crearon cuentas reales ni cambiaron credenciales para las pruebas.

## Archivos completos creados o modificados

- `backend/app/modules/access/administration_routes.py`: POST de colección,
  contexto obligatorio, slots compartidos, representación 201 y Location relativo;
  GET/PATCH conservan su contrato.
- `backend/app/main.py`: traducción central de UserCreationConflict a 409 saneado.
- `backend/tests/integration/test_user_creation_routes.py`: **56 casos nuevos**
  con peticiones HTTP y PostgreSQL real: perfiles objetivo, hash/tenant/auditoría,
  sesión del actor, contexto, denegaciones, payload/query, cookies/CSRF, límites,
  duplicados propios/ajenos/inactivos, selección pendiente, saturación y commit.
- `backend/tests/integration/conftest.py`: fixture compartida para rastrear y
  limpiar únicamente usuarios sintéticos de alta, dentro de la DB efímera protegida.
- `backend/tests/integration/proxy_access.py`: **un caso nuevo** de alta/detalle/
  conflicto/login de la nueva identidad por Nginx/Uvicorn reales. Canary en correo,
  contraseña y query prueba que los logs no los publican. Las peticiones ordinarias
  se espacian 0.12s entre instancias del navegador de tests; el caso final de
  saturación usa sockets sin ese espaciado para probar los límites reales.
- `docs/user-provisioning-contract.md`: contrato HTTP, respuestas, cabeceras,
  límites, canal asistido y riesgos, sobre el contrato interno de 2.13.
- `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/membership-http-contract.md`, `docs/data-model.md`, `docs/permissions.md`,
  `docs/decisions.md` y este registro: estado, decisiones, validación y siguiente.

## Comandos exactos ejecutados

Desde la raíz, recuperación y comprobación del volumen:

```bash
git status --short --branch
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
docker volume ls --filter name=carrillo-dev --format '{{.Name}}'
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-build proxy
```

Validación integral en entorno efímero, separado del volumen de desarrollo:

```bash
.venv/bin/python -m ruff format --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff check --fix --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
python3 -m scripts.ci > /tmp/carrillo-access-2.14-ci.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.14-final-ci.log 2>&1
git diff --check
```

Para repetir basta `python3 -m scripts.ci`. El runner genera credenciales y
proyecto únicos, valida sus guards, ejecuta tests/migraciones solo en carrillo_test
con tmpfs y elimina sus recursos. No ejecutar tests de integración sobre desarrollo.

Actualización local después del CI aprobado, sin migraciones nuevas:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

Rechazo anónimo adicional ejecutado, sin credenciales ni alta operativa:

```bash
python3 - <<'PY'
import json
from scripts.smoke_web import read
body, headers = read('http://localhost:8080', '/api/auth/memberships', expected=403, method='POST', headers={'Content-Type': 'application/json'})
assert json.loads(body) == {'detail': 'Request rejected'}
assert headers.get('Cache-Control') == 'no-store'
assert all(headers.get(key) is None for key in ('ETag', 'Location', 'X-Access-Membership', 'Set-Cookie'))
print('Local creation guard OK: anonymous POST without origin/CSRF rejected, no-store and no success metadata.')
PY
```

## Resultados reales

- Primer pipeline **carrillo-ci-adceaf21afce**: 592 backend y 90 frontend
  aprobados; proxy **5 passed, 1 failed**. El caso nuevo recibió 429 tras el caso
  que agotaba deliberadamente el límite compartido. No se cuenta como aprobado.
  Se espaciaron los recorridos ordinarios y se dejó la saturación como último
  caso. No se relajó Nginx ni se agregó retry de POST al producto o al test.
- Pipeline final **carrillo-ci-a5ffeaba5491**, exit 0, **CI passed**.
- **592 backend** en 41.28s: **214 unitarias + 378 PostgreSQL**. Más **6 casos
  por proxy real** en 4.61s, sin duplicarlos en el conteo.
- **90 frontend** en 1.09s y **13 scripts/configuración**: **701 pruebas** en
  total, **57 nuevas** respecto a 2.13 (56 HTTP/PostgreSQL y una por proxy).
- Ruff/formato en **65 archivos Python**, pip check, actionlint, ESLint,
  Prettier y tipos aprobados. Imágenes construidas; frontend sin cambios reutilizó
  capas de build ya validadas. No se afirma compilación frontend nueva sin caché.
- Los cuatro perfiles objetivo producen 201 con cuenta/membresía/auditoría
  confirmadas y hash real; tenant viene de la sesión. Location resuelve al detalle
  y su ETag coincide. El actor conserva cookies y sigue siendo su sesión original.
- No-admin, sesión inválida/revocada/caducada/inactiva, selección pendiente,
  contexto ajeno/ausente/inválido/duplicado, origen o CSRF falsos y cookies ambiguas
  se rechazan sin alta. No hay concesión por elegir role=admin ni tenant del cuerpo.
- Cuerpo y query inválidos devuelven error sin eco; contraseña de 15–128
  caracteres Unicode, espacios preservados y surrogate aislado rechazado. No
  se admiten campos de cuenta/tenant/hash/estado ni query de paginación en POST.
- Repetir alta da conflicto sin resetear contraseña; cuenta de otra empresa o
  inactiva tampoco se adjunta. Saturar los slots compartidos con login da 503
  sin registros; liberarlos permite crear. Fallo inyectado de commit revierte
  alta/auditoría y conserva cookies, sin devolver 201 o metadatos de éxito.
- Por proxy real: alta, detalle/ETag/Location, conflicto, query rechazada y login
  real de la identidad creada. Esa sesión tiene su perfil/tenant y no administra
  membresías. Solo un evento de alta. Inspección de logs del pipeline sin canary.
- Las pruebas previas de concurrencia, último admin, rollback y migraciones se
  volvieron a ejecutar. Esquema sigue en **0007_membership_creation_audit**, sin
  drift ni migración nueva. Los dos proyectos temporales se limpiaron.
- Desarrollo recuperado y actualizado: **cuatro servicios saludables**. Smoke
  exit 0: HTML español, diez assets, API mismo origen, 404 y rechazos sin CSRF/origen.
  POST anónimo adicional: 403/no-store sin cookies ni metadatos de éxito.
- Sin cambios visuales, recorrido nuevo de navegador, cuentas operativas creadas,
  restablecimiento de contraseña, publicación o push. No se probaron credenciales
  reales del usuario; el alta autenticada se validó solo con identidades efímeras.

## Riesgos pendientes y siguiente tarea

Falta el formulario de alta asistida. Debe usar la sesión/contexto verificados,
renovar CSRF, confirmar el estado solicitado y gestionar conflictos/resultado
incierto sin reintento. La contraseña debe quedar fuera de estado persistente,
Storage, logs y confirmaciones; retirarla al enviar/cancelar/perder contexto.
Ocultar el campo no protege frente a quien controla el equipo o navegador.

Sin invitación remota, verificación de correo, contraseña temporal, cambio inicial
forzoso, MFA o recuperación. Correo no disponible puede inferirse por conflicto
desde un admin autorizado. No hay idempotencia general ni RLS/defensa frente a
SQL privilegiado. Los slots de hashing son por proceso, no límite global; medir
capacidad antes de ampliar despliegue. Hito 2 continúa abierto.

TLS público, backups con restauración ensayada, primera CI remota, permisos de
módulos y mantenimiento de cuentas/auditoría siguen pendientes. La copia local
de 2.13 no se restauró en esta entrega. No se afirma preparación para producción.

Siguiente tarea **2.15**: formulario administrativo de alta asistida con
confirmación, limpieza de credenciales/contexto y errores 401/403/409/428/503,
pruebas de componente y recorrido real con cuentas sintéticas aisladas.
