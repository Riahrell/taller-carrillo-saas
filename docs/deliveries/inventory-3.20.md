# Inventario 3.20 — API privada de compras y recepción

Conecta alta/listado de proveedores, registro/listado/detalle de compras y
confirmación/consulta de recepción. Mantiene el stock transaccional e idempotente
del bloque anterior; no añade pagos o reglas fiscales. Todavía sin pantalla.

## Archivos

- `backend/app/modules/purchases/inputs.py`, `service.py`: paginación estricta,
  DTO de resumen/listado y lectura acotada por empresa; proveedor/compra/recibo.
- `purchases/receipts.py`: lectura de recepción con validación compartida.
- `purchases/routes.py`: rutas privadas, contexto, claves y respuestas mínimas.
- `backend/app/main.py`: servicios/router y errores seguros.
- `access/http_middleware.py`: 32 KiB solo en POST de compra; demás JSON 8 KiB.
- `ops/proxy/nginx.conf`: ubicaciones privadas, límites y errores normalizados.
- `backend/tests/integration/test_purchase_http.py`, `proxy_access.py`: MFA real,
  PostgreSQL, guardias y recorrido Nginx/Uvicorn real.
- Contrato HTTP de compras, README y esta entrega. Las rutas abreviadas de módulos
  anteriores están bajo `backend/app/modules/`.

## Comandos ejecutados

Desde la raíz:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_purchase_http.py --test-path tests/integration/test_purchase_service.py --test-path tests/integration/test_purchase_receipts.py --maxfail 3
python3 -m scripts.ci
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Primera corrida enfocada: 86 aprobadas y un fallo de fixture. Limpiar el campo
reauthenticated_at no vencía la prueba reciente del propio login MFA. Se corrigió
el reloj de prueba al vencimiento de cinco minutos; guardia runtime intacta.
CI final aprobada, código 0: **1.858 backend, 243 frontend, 11 proxy y 46
pruebas de scripts**. Lint, formato, TypeScript, builds, migraciones, smoke HTTP
y escaneos runtime/SBOM correctos. Cuatro imágenes runtime sin hallazgos; secretos
worktree/historial y locks Python sin hallazgos. Ruff: 225 archivos formateados;
git diff --check sin errores. Log `.local/validation/purchases-3.20-ci-final.log`.
Las cuatro alertas npm dev-only siguen bajo SEC-2026-001 hasta 2026-10-31
00:00 UTC; no se amplió la excepción. Recursos temporales limpiados.

Casos nuevos: contratos sin empresa/actor, listados paginados con timestamps
iguales y cursores ajenos, referencias/roles ajenos, CSRF/origen/contexto/clave,
JSON duplicado, recepción sin campos de stock, tamaño y chunks reales, compra
con 100 líneas y confirmación vencida. Proxy prueba registro -> recepción ->
repetición -> saldo, lectura privada y límites 32/8 KiB diferenciados.

## Límites y siguiente tarea

Sin formularios todavía; siguiente: interfaz de proveedores/compras con revisión
y reintentos manuales del mismo comando para respuestas inciertas. La API de
conteos sigue separada de la futura lectura unificada de movimientos.
Alta de proveedor sin idempotencia general; no repetir automáticamente.
No hay RLS contra SQL arbitrario del runtime. Sin migración nueva ni cambios en desarrollo/VPS, correo o almacenamiento externos.
