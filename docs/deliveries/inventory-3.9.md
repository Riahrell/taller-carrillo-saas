# Inventario 3.9 — Servicio transaccional de conteos

`InventoryAdministrator.record_count` registra evidencia, movimiento si hay
diferencia y saldo en una transacción. Obtiene empresa y actor de la sesión,
comprueba administrador/confirmación reciente al inicio y antes del commit.
Bloquea empresa antes de resolver producto/saldo, incluyendo el primer saldo
todavía inexistente. Reutilizar clave y contenido devuelve evidencia original;
cambiar contenido con la misma clave produce conflicto sin modificar stock.
No devuelve resultado antes del commit ni reintenta con una clave nueva.

## Archivos

- `backend/app/modules/inventory/service.py`: servicio, DTO y huella canónica.
- `backend/app/modules/access/administrator_guard.py`: guardia compartida extraída
  del catálogo sin modificar su política ni el módulo de administración existente.
- `backend/app/modules/catalog/service.py`: delega esa guardia conservando sus
  excepciones públicas y permisos de lectura/escritura.
- `backend/tests/integration/test_inventory_service.py`: autorización, rechazo de
  referencias ajenas, repetición, fallos SQL/commit, respuesta perdida después de
  commit, ámbito por empresa y concurrencia real con barrera antes del bloqueo.
- Contrato de conteos, README, modelo de datos y esta entrega.

## Comandos desde la raíz

```bash
python3 -m scripts.validate_identity --full-backend
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_service.py --test-path tests/integration/test_catalog_service.py
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

No hay nuevas migraciones ni cambios en la web; las pruebas usan exclusivamente
PostgreSQL efímero. No se aplicó nada a desarrollo ni Contabo. La primera validación
focalizada aprobó 54 pruebas; después se reforzó la sincronización de concurrencia
y se añadieron dos casos de recuperación/aislamiento. Esa primera ejecución no
sustituye la validación final.

Resultados finales ejecutados: 1.562 pruebas de backend aprobadas en 157,51
segundos; después, 56 pruebas focalizadas finales aprobadas en 5,32 segundos
(30 de inventario y 26 de catálogo, incluyendo los dos casos adicionales).
Ambas ejecuciones finalizaron con código 0, migración y `alembic check` correctos
y limpieza de sus recursos. No sumar ambos conteos como pruebas distintas.
Logs privados locales: `.local/validation/inventory-3.9-backend.log` y
`.local/validation/inventory-3.9-focused-final.log`. Ruff sin incidencias, 190
archivos con formato correcto y `git diff --check` sin errores. No se ejecutó
CI completo, frontend, navegador ni escaneo de imágenes en esta entrega.

Durante la extracción se detectó una colisión con el nombre del módulo de
administración existente; se restauró ese archivo y se usó `administrator_guard.py`
antes de la validación final. No quedó ningún cambio en `access/administration.py`.
Los cinco archivos experimentales de respaldo heredados siguen sin seguimiento;
no pertenecen a esta entrega y no se incluyeron en el commit.

## Riesgos y siguiente tarea

Servicio interno, sin rutas ni formulario de stock. La serialización por empresa
prioriza consistencia; limita paralelismo y puede devolver indisponibilidad al
agotar el tiempo de bloqueo. Ante fallo ambiguo conservar la misma clave y contenido.
Sin RLS ni protección frente a SQL arbitrario del runtime. Falta consulta operativa
de saldo/libro y contrato HTTP con CSRF, origen, contexto y mapeo seguro de errores.
Es la siguiente tarea antes de la interfaz. Imágenes Contabo y despliegue externo
siguen pendientes y no intervienen en la transacción de inventario.
