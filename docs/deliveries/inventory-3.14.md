# Inventario 3.14 — Asociación privada y recuperación de imágenes

Persistencia y servicio interno para asociar imágenes a producto/empresa con
sesión admin, reserva antes de S3, puntero/auditoría atómicos y limpieza reintentable.
La imagen anterior se conserva ante fallo o revocación. Sin endpoints ni cambios UI.

## Archivos

- `backend/app/modules/catalog/image_models.py`, `models.py`: reservas, puntero y
  auditoría inmutable; registro de modelos.
- `backend/migrations/versions/0034_product_images.py`: esquema, FK compuestas,
  checks y permisos mínimos por columna; downgrade rechaza datos de imágenes.
- `backend/app/modules/catalog/image_service.py`: contexto vigente, procesamiento,
  admisión, reserva, PUT/recuperación, asociación CAS, lectura y limpieza.
- `backend/tests/integration/test_product_image_service.py`: PostgreSQL real,
  transporte simulado y fallos de red/auditoría/commit/concurrencia.
- Pruebas existentes de catálogo, stock, MFA, provisionamiento y PostgreSQL:
  revisión esperada actualizada a `0034_product_images`, sin relajar controles.
- README, contrato de imágenes, modelo/permisos y esta entrega.

## Validación conjunta

Desde la raíz:

```bash
python3 -m scripts.validate_identity --full-backend --maxfail 3
python3 -m scripts.validate_identity --test-path tests/integration/test_product_image_service.py --maxfail 3
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Corrida conjunta backend: 1.663 aprobadas y un fallo en la sincronización del caso
concurrente nuevo. El segundo decoder era rechazado por el cupo de un proceso
antes de alcanzar la barrera de S3; se corrigió el test para iniciar la segunda
solicitud cuando la primera ya terminó de decodificar y entró al transporte.
No se cambió el cupo ni se relajó el servicio. Log:
`.local/validation/images-3.14-backend-final.log`.

Validación final del bloque, incluida esa corrección y diez casos adicionales:
**24 pruebas aprobadas** en PostgreSQL real, código 0. Migraciones y `alembic check`
correctos, recursos limpiados. Log `.local/validation/images-3.14-lifecycle.log`.
Se probaron estados/bytes/dimensiones/hashes inválidos, FK entre empresas, reserva
acotada, reemplazos concurrentes, revocación durante PUT/GET, rollback de auditoría,
respuestas perdidas tanto de S3 como del commit DB y limpieza reintentable sin
borrar la imagen actual. No se repitió el backend completo después de corregir
exclusivamente la sincronización del test; no se afirma una corrida completa verde.

Ruff correcto, 201 archivos formateados y `git diff --check` sin incidencias.
Escaneos de secretos worktree/historial y locks Python sin hallazgos; cuatro avisos
npm de desarrollo siguen aceptados bajo SEC-2026-001 hasta 2026-10-31 00:00 UTC,
sin extender excepción. Reporte `.local/security-reports/e5155c08a52e4ec894fa55240dd1a5ac.json`.
No hubo cambios de dependencias, frontend ni de adaptador S3; no se repitieron sus
pruebas de navegador o conectividad real.
El primer intento de migración detectó nombres únicos duplicados generados por
convención; se nombraron explícitamente las restricciones afectadas y se corrigió
también el nombre de la FK de imagen anterior en auditoría.

## Pendiente

Reconciliador durable programado y controles operativos de recursos; adaptación
HTTP segura y UI de carga/lectura. La limpieza debe volver a visitar tombstones
porque DB y S3 no comparten transacción, incluso tras un PUT incierto tardío.
No se aplicó la migración a desarrollo/VPS y no se conectó Contabo en esta fase.
Las credenciales siguen fuera del repositorio. No se alteran precio, costo ni stock.

Siguiente tarea: worker de reconciliación y configuración, antes de habilitar HTTP.
