# Inventario 3.13 — Worker limitado y transporte privado Contabo

Bloque conjunto: ejecución del decoder en subproceso Linux con límites de memoria,
CPU, tiempo y concurrencia; transporte privado S3 configurable y smoke real con
limpieza de objetos sintéticos. No hay carga web ni asociación de imágenes en DB.

## Archivos

- `backend/app/modules/catalog/image_processing.py`, `image_worker.py`: ejecución
  acotada, entorno limpio, timeout con kill/wait y cupo sin cola.
- `backend/app/modules/catalog/image_storage.py`: configuración explícita, reserva
  de clave opaca, PUT condicional privado, lectura verificada y borrado interno.
- `backend/app/tools/check_product_storage.py`: prueba operativa aislada.
- `backend/tests/test_image_processing.py`, `test_image_storage.py`,
  `test_product_storage_check.py`: subprocess real, límites, timeout y recolección,
  SDK simulado con Stubber, integridad, errores y limpieza del smoke.
- `backend/pyproject.toml`, ambos locks: boto3 1.43.114 y transitivas con hashes.
- README, contrato de imágenes y esta entrega.

## Comandos y evidencia

Desde raíz:

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_product_images.py backend/tests/test_image_processing.py backend/tests/test_image_storage.py backend/tests/test_product_storage_check.py -q
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
python3 -m scripts.validate_identity --test-path tests/test_product_images.py --test-path tests/test_image_processing.py --test-path tests/test_image_storage.py
python3 -m scripts.security
docker build --target runtime -t carrillo-images-3.13:validation backend
python3 -m scripts.image_security --image carrillo-images-3.13:validation
git diff --check
```

Desde `backend/`, solo con autorización para crear/eliminar un objeto sintético:

```bash
../.venv/bin/python -m app.tools.check_product_storage --secret-file /home/riahrell/.config/taller-carrillo/contabo-s3.env
```

55 pruebas locales aprobadas; 51 del procesador/worker/transporte aprobadas dentro
del contenedor Linux real. Las cuatro pruebas finales del smoke se ejecutaron
localmente después de ese build. Migraciones/check en PostgreSQL efímero correctos,
recursos limpiados. Ruff correcto, 197 archivos formateados.
Log de contenedor: `.local/validation/images-3.13-container.log`.

Contabo real: PUT/GET e integridad correctos, segunda escritura rechazada y acceso
anónimo HTTP 401. El criterio inicial solo aceptaba 403: se corrigió para aceptar
401/403 y se probó rechazo de 200/404. Todas las ejecuciones confirmaron limpieza
del objeto sintético. Log final `.local/validation/images-3.13-contabo.log`.
No se imprimieron claves, cuerpos ni URLs firmadas; no se tocó la VPS.

Escaneos de secretos worktree/historial y locks Python sin hallazgos. Persisten
cuatro avisos npm de desarrollo bajo SEC-2026-001 hasta 2026-10-31 00:00 UTC,
sin extender excepción. Reporte privado:
`.local/security-reports/5b1cf80bb09447e4ba4dff4e4ad1154f.json`.
Imagen backend runtime: cero hallazgos, SBOM/evidencia en
`.local/image-reports/6c3c7ec472184a21ae74aab7730f54f1`.
El primer intento de escaneo encontró eliminada la imagen efímera por la limpieza
del validador; se reconstruyó runtime y el escaneo final terminó con código 0.
No se volvió a ejecutar la suite completa de frontend/autenticación.

## Pendiente

Asociación autorizada en DB, journal de reservas y recuperación/limpieza segura,
cuotas, ruta privada de carga/lectura y UI. Los rlimits no aíslan permisos de
filesystem/red; la habilitación debe revisar montajes, secretos y presupuesto de
recursos del contenedor. No declarar imágenes operativas en la web con este bloque.
Siguiente tarea: persistencia del ciclo de vida y servicio autorizado por empresa.
