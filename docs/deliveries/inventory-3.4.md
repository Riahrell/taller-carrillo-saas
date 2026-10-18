# Inventario 3.4 — API administrativa de catálogo

Altas existentes conectadas a HTTP; listados paginados de productos/categorías
y detalle de producto limitados a la empresa de la sesión. Solo administración,
sin vitrina pública ni permisos operativos concedidos implícitamente a otros roles.
Lecturas sin step-up; altas conservan autorización y confirmación reciente,
auditoría atómica y rechazo de datos de autoridad/stock del cliente.

Contrato de rutas/DTO/errores: `../catalog-http-contract.md`. Sin migración nueva.
No se aplicaron migraciones en desarrollo ni VPS; validación con datos sintéticos
en entornos Docker efímeros. Interfaz y existencias todavía pendientes.

## Archivos cambiados

- `backend/app/modules/catalog/inputs.py`: paginación acotada y canónica.
- `backend/app/modules/catalog/service.py`: consultas autorizadas, DTO con
  contexto interno excluido de JSON, distinción entre sesión inválida y permiso
  denegado. Ninguna consulta recibe una empresa elegida por cliente.
- `backend/app/modules/catalog/routes.py`: GET/POST, intención de navegador,
  contexto esperado, cookies y comprobación del peer del proxy.
- `backend/app/main.py`: servicio/router y traducción segura de errores.
- `ops/proxy/nginx.conf`: límites y normalización de catálogo, logs sin query ni
  cuerpos/cabeceras, no-store también ante errores del proxy. El generador TLS
  reutiliza esta plantilla.
- `backend/tests/test_catalog_inputs.py`,
  `backend/tests/integration/test_catalog_http.py`,
  `backend/tests/integration/proxy_access.py`: límites, MFA HTTP real,
  permisos, aislamiento, CSRF, paginación, proxy y cuerpo excesivo.
- README, contrato HTTP y actualizaciones de contrato/permisos/esta entrega.

## Comandos ejecutados desde la raíz

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_catalog_http.py --test-path tests/integration/test_catalog_service.py --test-path tests/test_catalog_inputs.py --test-path tests/test_app.py
python3 -m scripts.ci
python3 -m scripts.validate_tls_proxy
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Validación enfocada: **115 pruebas pasan**, runner termina con código 0 y elimina
su entorno. Plantilla TLS: nginx -t pasa como UID 101 con certificados temporales;
no prueba certificado público, renovación ni dominio remoto. Ruff y formato pasan.
Una ejecución local previa de pytest con TestClient quedó bloqueada y se terminó
con señal (143); no se contó como éxito. La validación válida es la de Docker.

Logs privados: `.local/validation/catalog-3.4-targeted.log`, `catalog-3.4-ci.log`
y `catalog-3.4-tls-syntax.log`. No sumar recuentos de ejecuciones como si fueran
pruebas distintas.

CI terminó con código 0: **1453 backend, 156 frontend, 8 proxy real y 52 scripts**
del worktree. Los 52 incluyen pruebas de las herramientas de respaldo aún no
versionadas; no presentar ese recuento como el de un checkout limpio. Workflow,
lint/formato, tipos, builds, migraciones, smoke HTTP y escaneos de secretos/locks
pasan. Cuatro imágenes runtime con **0 hallazgos** y SBOM generados. npm conserva
cuatro avisos dev-only bajo SEC-2026-001; no se resolvieron ni se extendió su plazo.
El entorno CI se retiró al terminar. Esta evidencia no prueba aceptación remota,
recuperación del despliegue anterior ni recorrido de navegador del catálogo.

## Límites y siguiente tarea

Sin edición/desactivación, búsquedas, stock, costeo, compras ni publicación.
Listado incluye inactivos para gestión admin. Paginación viva por UUID, sin total
global ni garantía de snapshot entre páginas. No hay idempotencia de altas: el
cliente no debe repetir automáticamente un POST con respuesta perdida.

Siguiente: interfaz administrativa para listar y crear productos/categorías,
con reautenticación existente y descarte de datos ante cambio/pérdida de contexto.
VPS/HTTPS y restauración remota siguen pospuestos; excepción dev-only
SEC-2026-001 conserva su fecha de vencimiento. Herramientas anteriores de respaldo
permanecen sin seguimiento de Git y fuera de esta entrega; no se declara un hito
cerrado ni el repositorio completo limpio.
