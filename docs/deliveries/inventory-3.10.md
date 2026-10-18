# Inventario 3.10 — API administrativa y consultas

API privada de saldo, costo promedio, historial de conteos y registro idempotente
de conteos. El costo unitario se calcula en servidor y se distingue del precio de
venta del catálogo. Costos y cantidades exactas no forman parte de la futura
vitrina pública. Contrato completo: `../inventory-http-contract.md`.

## Archivos

- `backend/app/modules/inventory/routes.py`: rutas privadas y clave canónica.
- `backend/app/modules/inventory/service.py`: consultas autorizadas, historial
  cronológico, promedio de consulta y serialización decimal estable en reintentos.
- `backend/app/modules/inventory/inputs.py`: consulta paginada estricta.
- `backend/app/modules/inventory/models.py` y
  `backend/migrations/versions/0033_stock_history.py`: índice de historial.
- `backend/app/main.py`: integración del servicio/router y errores seguros.
- `ops/proxy/nginx.conf`: ubicación privada, cuotas/cuerpo y errores no cacheables.
- `backend/tests/integration/test_inventory_http.py`: login real con contraseña y
  TOTP, lecturas, escritura, reintento, permisos, cursores, costo promedio y errores.
- `backend/tests/integration/proxy_access.py`: flujo adicional por Nginx/Uvicorn.
- Tests de catálogo, MFA, PostgreSQL, creación de usuarios y esquema de inventario:
  expectativas de head actualizadas a `0033_stock_history`.
- README, contratos de inventario, modelo, permisos, aceptación y esta entrega.

## Comandos desde la raíz

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_http.py --test-path tests/integration/test_inventory_service.py
python3 -m scripts.ci
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

La primera prueba focalizada aprobó 58 casos antes de añadir el promedio visible y
las últimas pruebas. El primer CI falló al detectar como posible API key una llamada
de prueba que combinaba `key=key` con un argumento literal; se inspeccionó el código
y solo contenía referencias a UUID sintéticos. Se explicitó el argumento de cambio
mediante un diccionario, sin desactivar reglas ni añadir exclusiones al detector.
Ese CI no ejecutó el resto de validaciones. Resultado privado:
`.local/security-reports/8da0e8901f4c490e9c6f471798451ec5.json`.

Resultado final: CI completo con código 0 en
`.local/validation/inventory-3.10-ci-final.log`. Backend: 1.595 pruebas aprobadas;
frontend: 189; Nginx/Uvicorn real: 9, incluida la nueva operación de inventario.
Migraciones, `alembic check`, workflow, lint, formato, tipos, builds y smoke correctos.
Cuatro imágenes runtime con cero hallazgos de cualquier severidad; evidencia y
SBOM en `.local/image-reports/c566e283f2284b4a80dc09a55b617d32/`. Recursos efímeros
limpiados al terminar. No hubo QA manual de navegador porque no cambió la interfaz.

El escaneo final de secretos e historial no encontró hallazgos; locks Python sin
hallazgos. Los cuatro avisos npm dev-only conservan la excepción SEC-2026-001 hasta
2026-10-31 00:00 UTC; no se amplió ni se declara resuelta. El runner aprobó 52 tests
de scripts del worktree, incluidos seis de los archivos experimentales heredados
sin seguimiento (46 corresponden al repositorio seguido). No se incorporan esos
cinco archivos de respaldo al commit de inventario. Ruff local también aprobó,
con 193 archivos en formato correcto y diff sin errores.

## Límites y siguiente tarea

Sin interfaz nueva ni despliegue: desarrollo y VPS no fueron migrados. Solo el
runner de validación aplica cambios a bases efímeras. La pantalla privada prevista
es `/inventario`; `/productos` será la vitrina pública, sin publicación automática
por alta de inventario. La ruta administrativa `/catalogo` existente conserva su
función. Imágenes en Contabo Object Storage aún pendientes de integración.

Lecturas y escrituras se serializan inicialmente por empresa; conservan los
límites de bloqueo/sentencia existentes. Paginación viva, no snapshot entre páginas.
El historial de esta entrega es de conteos físicos; otros orígenes de movimientos
llegarán con compras/consumos/ventas. No hay herramienta operativa de reconciliación
ni RLS ante SQL arbitrario. Siguiente tarea: pantalla de inventario con sesión,
consulta de saldo/costo/historial y revisión de conteos, conservando clave y
contenido cuando el resultado sea incierto.
