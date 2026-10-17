# Entrega: API de confirmación reciente (acceso 2.17)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-reauthentication-api`.

## Resultado

POST `/api/auth/reauthenticate` conecta el servicio de 2.16. El contrato se
documentó antes de implementar la ruta. Solo recibe contraseña actual en JSON,
sin query, con cookie privada de sesión, Origin/CSRF firmados y membresía esperada
obligatoria. La membresía compara contexto; no selecciona usuario/empresa.
El servicio relee identidad/perfil admin y conserva su transacción/límites.

204 sin cuerpo únicamente tras commit, con X-Access-Membership y cookies de
sesión/CSRF nuevas. El token anterior queda inválido; el nuevo conserva la
expiración absoluta. HttpOnly para sesión, Secure/prefijo __Host- en HTTPS,
SameSite=Lax, Path=/ y sin Domain. Las pruebas HTTPS del cliente ASGI comprueban
flags; no representan un despliegue TLS remoto.

IP desde proxy confiable, dos slots de hashing compartidos con login/alta y
cuotas persistentes de cuenta/IP del login. Rechazos de credencial/sesión/permiso
devuelven 401 genérico sin borrar cookies o publicar actor. No se envían tokens,
huellas, contraseña o datos personales en JSON. CSRF viejo no vale con token nuevo.
No hay retry automático; pérdida de respuesta de rotación requiere login.

El middleware compartido ahora rechaza claves JSON duplicadas en cualquier nivel:
400 saneado antes de escribir, también para rutas existentes. Cookies ambiguas
o sesión mezclada con autenticación pendiente impiden confirmación.

**Falta el formulario y activar la guardia dentro de alta/cambio administrativos.**
Una sesión válida todavía basta para esas operaciones: esta entrega no cierra
esa brecha ni equivale a MFA o preparación para producción.

## Archivos completos creados o modificados

- `backend/app/modules/access/routes.py`: POST con contexto, sesión/CSRF,
  slots, proxy y publicación de cookies tras commit, sin cuerpo de éxito.
- `backend/app/modules/access/http_context.py`: comparación UUID canónica común.
- `backend/app/modules/access/administration_routes.py`: reutiliza esa comparación,
  conservando contratos y autoridad existentes de POST/PATCH.
- `backend/app/modules/access/http_middleware.py`: rechazo de claves JSON repetidas.
- `backend/tests/integration/test_reauthentication_routes.py`: **67 casos nuevos**
  con HTTP/PostgreSQL: rotación, flags, permisos/contexto, sesión, validación,
  errores/rollback, slots y cuotas compartidas.
- `backend/tests/integration/proxy_access.py`: **un caso nuevo** con proxy/backend
  reales, rotación, replay, CSRF antiguo, peer directo, canary y logout.
- `backend/tests/test_http_security.py`: **dos casos nuevos** que comprueban
  rechazo de JSON duplicado antes de cualquier efecto en un endpoint de prueba.
- `docs/reauthentication-contract.md`: contrato HTTP completo y siguientes pasos.
- `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/permissions.md`, `docs/user-provisioning-contract.md`,
  `docs/security-review.md`, `docs/decisions.md`, `docs/staging-plan.md` y este
  registro: estado, controles activos/pendientes y autorización de limpieza VPS.

Archivos completos en el repositorio. Sin cambios de frontend, dependencias,
configuración de secretos o migraciones: esquema permanece en 0008.

## Comandos exactos ejecutados

Desde la raíz del repositorio:

```bash
.venv/bin/python -m ruff check backend scripts
.venv/bin/python -m ruff format --check backend scripts
git diff --check
timeout 60s .venv/bin/python -m pytest backend/tests/test_http_security.py -q
python3 -m scripts.ci > /tmp/carrillo-access-2.17-ci.log 2>&1
```

El runner genera su propio proyecto, DB PostgreSQL en tmpfs y credenciales
temporales. Sus guards rechazan bases operativas y limpian recursos al terminar.
No cambiar esas comprobaciones ni ejecutar suites con cuentas reales.

Actualización del backend local, conservando entorno/credenciales existentes:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.17-dev-build.log 2>&1
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

No hace falta generar nuevas credenciales ni aplicar migraciones para esta
actualización. No se probó la reautenticación usando la cuenta real del operador.

## Resultados reales

- La ejecución local del archivo de middleware dentro del sandbox quedó
  bloqueada después de dos casos; se interrumpió con exit 130. No se contó como
  aprobada. Repetición fuera del sandbox, con límite 60s: **45 passed en 1.37s**.
- Único pipeline integral de esta entrega, **carrillo-ci-8307a93b3899**, exit 0,
  **CI passed** en el primer intento.
- **715 backend en 55.74s**: **228 unitarios y 487 PostgreSQL**. Más **7 por
  proxy en 5.83s**, contados una sola vez; **129 frontend en 1.24s** y **13
  scripts/configuración**. Total **864 pruebas**, **70 nuevas** frente a 2.16.
  Los 45 casos locales están incluidos en backend; no se suman de nuevo.
- Ruff/formato de 71 archivos Python, pip check, actionlint, ESLint, Prettier,
  tipos, migraciones y builds aprobados. Frontend sin cambios reutilizó build
  en caché; no se afirma una compilación nueva de Next.js.
- 204 con contexto y cookies comprobadas; misma fila/identidad/empresa/expiración,
  token nuevo y prueba interna válida. Contraseña Unicode con espacios preservados.
  HTTP/HTTPS simulados verifican alcance/flags de cookies y exclusión del secreto.
- Token viejo produce rechazo sin borrar cookies nuevas; prueba vieja mezclada
  con token nuevo se rechaza. Estado/revocación/inactividad/expiración/perfil o
  actor ajeno no rotan ni tocan actividad. Preauth/selección pendiente no autorizan.
- Origen/CSRF/headers/cookies ambiguos, contexto requerido/canónico, campos extra,
  Unicode inválido, query, cuerpo excesivo o ambiguo y tipo de contenido se
  rechazan sin efectos del servicio ni eco del canary. Pruebas anteriores de
  administración verificaron que extraer el comparador conserva sus contratos.
- Saturar slots devuelve 503 sin entrar al servicio; liberar capacidad permite
  confirmar. Fallo inyectado de commit devuelve 503 sin cookies/contexto y revierte
  sesión/prueba/contadores; una petición explícita posterior tiene capacidad.
  Cinco fallos de confirmación agotan también la cuota del login.
- Nginx/Uvicorn reales: confirmación con rotación, guardia interna verificable,
  peer directo rechazado pese a cookies reales sintéticas, token/proof viejos
  rechazados, query saneada y logout de la sesión nueva. Inspección CI de logs
  sin el canary de contraseña/query. Límites del proxy siguieron probándose.
- Proyecto temporal eliminado. No hubo nuevo recorrido manual de navegador:
  esta entrega cambia API y middleware, sin interfaz nueva.
- Local actualizado: **cuatro servicios saludables**. Smoke exit 0: HTML español,
  diez assets, API mismo origen, 404 y rechazo de escritura sin origen/CSRF.
  Sin cambios de cuentas o contraseñas reales, bootstrap o regeneración de secretos.

## Riesgos y siguiente tarea

**2.18**: formulario de confirmación con contraseña fuera de estado persistente,
limpieza al perder contexto/visibilidad, coordinación de rotación entre pestañas,
resultados inciertos sin replay, y guardia exigida en las transacciones de
alta/cambio administrativos. Probar bloqueos sin efectos y recorrido completo.
No sustituir la guardia por una comprobación frontend o preflight separado.

La confirmación sigue siendo por contraseña, con ventana de cinco minutos, sin
MFA ni permiso de operación de un solo uso. Logout con token ya invalidado no
identifica al token nuevo; coordinar escrituras en UI. Auditoría ampliada,
recuperación, dependencias/secretos, navegador y restauración de backups siguen
pendientes en la revisión de seguridad. La copia privada de 2.16 no se restauró
en esta entrega. No hay certificación o evaluación ASVS completa.

El usuario autoriza dejar limpia la VPS si hace falta: sustituye conservar
obligatoriamente su despliegue anterior. Queda registrado en `../staging-plan.md`;
todavía se requiere identificar recursos y preparar respaldo antes de retirarlos.
Sin SSH, limpieza, cambio DNS, verificación TLS, despliegue o push remoto realizados.
No hace falta comprar servicios para esta entrega.
