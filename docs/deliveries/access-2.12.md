# Entrega: pantalla de membresías existentes (acceso 2.12)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-membership-screen`.

## Resultado

El administrador puede abrir `/membresias` desde su sesión, consultar accesos de
su empresa, revisar un detalle actual y confirmar un cambio de rol/estado. Una
página de hasta 25 filas, avance por cursor, vuelta al principio y actualización.
Sin selección de empresa desde esta pantalla, altas ni cambios de cuenta global.
Los estados de membresía y cuenta se distinguen; activar el primero no activa
la cuenta. No se agregan reglas comerciales, impuestos ni operaciones de stock.

El formulario exige detalle recién consultado y ETag, un estado diferente,
revisión del resumen y confirmación explícita. Cancelar/volver a editar no
escribe. Mutex y controles deshabilitados impiden doble envío desde el componente.
PATCH renueva CSRF, envía If-Match y usa el servicio transaccional existente.
409/412 bloquean el borrador hasta otra lectura/revisión explícita; otros fallos
retiran contexto con aviso de resultado incierto. No reintenta escrituras.
Tras éxito ajeno comprueba sesión y relee la primera página antes de informar
éxito; tras cambio propio comprueba acceso después de que el servidor retire
cookies. Un abort o timeout no promete revertir un commit.

Se añadió X-Access-Membership en los éxitos administrativos para vincular cada
respuesta al actor que autorizó la operación. Sale del servicio después de commit,
excluido del JSON y del ETag. El cliente rechaza respuestas de otro contexto.
PATCH admite X-Expected-Membership opcional por compatibilidad; la pantalla
siempre lo envía. El servicio lo compara con el actor autenticado dentro de la
transacción, antes de If-Match/escritura. No selecciona actor ni tenant, no da
permiso y no reemplaza sesión/rol/CSRF. Sin cambios de esquema o dependencias.

La entrada por URL directa comprueba sesión; otros perfiles no montan lista.
Focus/pageshow/visibilidad y la señal `changed` retiran/recomprueban contexto,
abandonando lecturas tardías. Interrumpir un guardado informa resultado incierto.
No Storage ni polling; timer visual de 30 minutos sin mantener viva la sesión.
Los controles del servidor siguen siendo la autoridad de cada operación.

## Archivos completos creados o modificados

- `frontend/src/app/membresias/page.tsx`: ruta y shell inicial sin datos personales.
- `frontend/src/components/membership-workspace.tsx`: puerta de sesión/admin,
  recuperación, limpieza de contexto, señales entre pestañas y caducidad visual.
- `frontend/src/components/membership-manager.tsx`: lista, paginación, revisión,
  confirmación, conflicto y refresco tras cambio.
- `frontend/src/lib/memberships/api.ts`: DTO mínimos comprobados, UUID/roles/
  estados/cursor/ETag, vínculo al actor y PATCH con CSRF sin reintentos.
- `frontend/src/lib/api/client.ts`: transporte PATCH acotado y cabeceras de
  respuesta disponibles al decoder; conserva same-origin/no-store/abort/timeout.
- `frontend/src/components/access-panel.tsx`: enlace para la sesión admin actual.
- `frontend/src/app/globals.css`: estilos adaptables, formulario y foco visible.
- `backend/app/modules/access/administration.py`: captura del actor autorizado y
  comparación transaccional del contexto esperado, sin nuevas columnas.
- `backend/app/modules/access/administration_routes.py`: cabeceras de contexto y
  validación de precondición opcional, sin ampliar autoridad del cliente.
- `frontend/tests/memberships-api.test.ts`: 19 casos nuevos del transporte/DTOs,
  CSRF/If-Match, contexto y errores sin repetir PATCH.
- `frontend/tests/membership-workspace.test.tsx`: 20 casos nuevos de perfiles,
  revisión/confirmación/cancelación, doble envío, conflictos, páginas, respuestas
  tardías, contexto propio, caducidad y señales entre pestañas.
- `backend/tests/integration/test_membership_routes.py`: cuatro casos nuevos de
  contexto actual/ajeno/inválido y cabecera de respuesta controlada por servidor;
  amplía asserts de detalle/PATCH existentes sin alterar el JSON.
- `backend/tests/integration/proxy_access.py`: comprueba cabecera de contexto de
  lista y PATCH con precondición por Nginx/Uvicorn reales, dentro de caso existente.
- `backend/tests/integration/browser_fixture.py`: segundo acceso sintético en
  empresa A y ventana manual de 30 minutos, archivo de credenciales privado.
- `scripts/browser_qa.py`: harness explícito que reutiliza aislamiento/limpieza
  del pipeline, base tmpfs, puerto loopback dinámico y fixture manual.
- `README.md`, `docs/acceptance.md`, `docs/auth-http-contract.md`,
  `docs/membership-http-contract.md`, `docs/permissions.md`, `docs/decisions.md`,
  `docs/data-model.md` y este registro: estado, contrato, decisiones y límites.
- `docs/deliveries/access-2.12-memberships.jpg`: evidencia visual con cuentas
  sintéticas, tras cambio confirmado y listado actualizado. No son cuentas reales.

## Comandos exactos de validación

Desde la raíz, validación integral repetible:

```bash
.venv/bin/python -m ruff format --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
python3 -m scripts.ci > /tmp/carrillo-access-2.12-final-ci.log 2>&1
git diff --check
```

El pipeline comprueba workflow, scripts, Ruff/formato, pip check, ESLint,
Prettier, tipos y tests. Construye las imágenes y ejecuta upgrade/check/
downgrade/upgrade y pruebas de PostgreSQL solo en la DB temporal protegida.
Incluye cinco casos por proxy real y smoke HTTP. Limpia sus recursos al finalizar.

QA manual realmente ejecutado:

```bash
python3 -m scripts.browser_qa > /tmp/carrillo-access-2.12-browser.log 2>&1
docker exec carrillo-ci-f0d88a6df8ba-browser-fixture touch /tmp/carrillo-browser-finished
```

El segundo comando registra el contenedor de esta ejecución; una repetición usa
el nombre aleatorio que imprima el harness. No reutilizar ese nombre ni el puerto.
La credencial sintética se leyó del archivo 0600 dentro del contenedor del fixture,
sin registrarla aquí ni crear una cuenta operativa. El navegador accedió a la URL
loopback emitida y realizó login, selección explícita de empresa A, apertura de
lista, intento de degradar al último administrador (rechazo), cancelación de esa
revisión, cambio Cajero → Mecánico de la otra membresía y confirmación del resultado.
La captura muestra el éxito y el estado releído. No se validó manualmente toda
la matriz de perfiles/resoluciones ni la autorrevocación: tienen cobertura automática.

Actualización local ejecutada, sin migraciones ni altas de usuarios:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend frontend frontend-tests
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

Comprobación anónima adicional ejecutada:

```bash
python3 - <<'PY'
from scripts.smoke_web import read
import json
body, _ = read('http://localhost:8080', '/membresias')
assert 'Membresías' in body.decode()
assert 'Comprobando acceso' in body.decode()
assert '@example.com' not in body.decode()
body, headers = read('http://localhost:8080', '/api/auth/memberships?limit=25', expected=401, headers={'X-Requested-With': 'XMLHttpRequest', 'Sec-Fetch-Site': 'same-origin'})
assert json.loads(body) == {'detail': 'Access denied'}
assert headers.get('Cache-Control') == 'no-store'
assert headers.get('X-Access-Membership') is None
assert headers.get('Set-Cookie') is None
print('Membership screen OK: static shell without personal data; anonymous API 401/no-store without actor or cookies.')
PY
```

## Resultados reales

- Pipeline final **carrillo-ci-cb0eea42c5b3**, exit 0, **CI passed**.
- **480 backend** en 31.29s: 193 unitarias y 287 con PostgreSQL. Más **5 por
  proxy real** en 0.68s; no duplicar estos cinco en el conteo.
- **90 frontend** en 1.14s y **13 scripts/configuración**: **588 pruebas** totales,
  **43 nuevas** respecto a 2.11 (39 frontend y cuatro PostgreSQL). El caso proxy
  ampliado y el fixture de QA manual no se suman como casos nuevos funcionales.
- Ruff/formato de **61 archivos Python**, pip check, actionlint, ESLint, Prettier
  y tipos aprobados. Migraciones siguen en `0006_membership_audit`, sin drift.
- La primera ejecución de esta entrega, **carrillo-ci-e74d6d5bf56a**, también
  aprobó y ejecutó compilación real Next.js: `Compiled successfully`, incluida
  `/membresias`. El pipeline final reutilizó la capa de producción ya construida;
  el código de producto no cambió entre ambas, se ampliaron pruebas y harness.
- Dos intentos locales de pruebas frontend detectaron esperas incorrectas del
  test: uno observaba el aviso intermedio de guardado como si fuera éxito y otro
  comprobaba foco antes del efecto de React. Se corrigieron las esperas a resultado
  concreto/foco eventual. No se cuentan esos intentos como aprobados. El conjunto
  final de 90 casos aprobó en el pipeline.
- QA **carrillo-ci-f0d88a6df8ba**: fixture terminó con 1 passed en 374.97s
  (incluye espera manual, no mide rendimiento). Borró archivo privado y eliminó
  contenedores, redes e imágenes propias. Ningún usuario de desarrollo modificado.
- Desarrollo actualizado: **cuatro servicios saludables**. Smoke exit 0: HTML
  español, diez assets, API mismo origen, 404 y rechazos sin origen/CSRF. Pantalla
  anónima entrega shell sin datos y listado API devuelve 401/no-store sin actor
  ni cookies. No se inició sesión real en desarrollo para validar esta entrega.
- Sin migraciones nuevas, dependencias nuevas, publicación ni push. Los secretos
  locales no se regeneraron ni se añadieron a Git. Evidencia visual con viewport
  observado de 648×880; no se afirma una matriz completa de dispositivos.

## Riesgos pendientes y siguiente tarea

Hito 2 sigue abierto: altas, operaciones globales de cuenta/contraseña, permisos
de módulos, consulta/ampliación de auditoría, recuperación y limpieza de acceso.
El bootstrap operativo sigue siendo una acción explícita del operador, sin
credencial predeterminada. La pantalla administra membresías ya existentes.

No hay RLS ni defensa frente a SQL arbitrario privilegiado. Una pestaña visible
sin interacción puede mostrar un estado anterior a una revocación remota hasta
su próxima comprobación; no hay canal push del servidor. Páginas vivas sin total
ni snapshot, ETag de estado sin versión histórica. Un timeout puede ocultar un
commit: comprobar sesión/estado, sin repetir cambios automáticamente. La
precondición de actor adicional es opcional en HTTP para clientes anteriores;
no es una barrera de autorización alternativa a la sesión del servidor.

TLS público, capacidad, backups/restauración y primera CI remota siguen pendientes.
No se afirma preparación para producción ni modo offline/sincronización. Los
servicios localhost pueden seguir funcionando sin internet si equipo/Docker
siguen encendidos; chat y descargas sí necesitan conexión.

Siguiente tarea **2.13**: contrato y servicio de alta administrativa controlada
de usuarios/membresías, con decisiones documentadas antes de añadir la siguiente
porción pequeña; sin registro público, credenciales por defecto ni ampliación
implícita de acceso a otras empresas. Probar aislamiento, auditoría, duplicados,
rollback y concurrencia antes de conectarlo a una pantalla.
