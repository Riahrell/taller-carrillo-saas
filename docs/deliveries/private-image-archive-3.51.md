# 3.51 — Archivo privado de fotos y recuperación condicional

Se añade una herramienta administrativa para exportar las fotos vigentes y sus
miniaturas desde las referencias de PostgreSQL, verificarlas offline y restaurar
sus objetos sin sobrescribir datos existentes. La exportación consulta en una
transacción SQL de solo lectura REPEATABLE READ. Mantenimiento y dump coordinado
siguen siendo responsabilidad explícita del operador.

Las copias tienen carpeta 0700 y archivos 0600. No se aceptan enlaces simbólicos,
archivos públicos, claves ajenas al formato interno, duplicados ni hashes/tamaños
incorrectos. Verify valida todos los archivos antes de permitir el primer PUT de
restore. Los PUT usan el transporte privado existente y su escritura condicional;
se comprueba el contenido restaurado y se permite repetir objetos idénticos.
Un fallo de exportación no publica manifest de completitud. No se borran objetos.

Archivos completos:

- backend/app/tools/image_archive.py
- backend/tests/test_image_archive.py
- backend/tests/integration/test_product_image_variants.py
- docs/operations/private-image-backup.md
- docs/inventory-release-plan.md
- docs/deliveries/private-image-archive-3.51.md

Validación ejecutada desde raíz:

```sh
.venv/bin/ruff check backend/app/tools/image_archive.py backend/tests/test_image_archive.py backend/tests/integration/test_product_image_variants.py
.venv/bin/ruff format --check backend/app/tools/image_archive.py backend/tests/test_image_archive.py backend/tests/integration/test_product_image_variants.py
PYTHONPATH=backend .venv/bin/python -m app.tools.image_archive --help
python3 -m scripts.validate_identity --test-path tests/test_image_archive.py --test-path tests/integration/test_product_image_variants.py --maxfail 3
```

Resultado: 14 pruebas aprobadas; migraciones y Alembic check sin cambios pendientes;
Ruff y ayuda CLI correctos. El recorrido PostgreSQL selecciona solo las variantes
vigentes, excluye una foto sustituida y comprueba exportación, recuperación y
repetición, con decodificación de WebP real. Otros casos rechazan corrupción del
último archivo antes de escribir, symlinks, permisos públicos, conflictos,
referencias duplicadas/traversal y exportación parcial. S3 es simulado, PostgreSQL
es real y temporal. Log ignorado: .local/validation/image-archive-3.51.log.

No se usaron credenciales de Contabo ni se enviaron mensajes. No se modifica la
interfaz ni el estado de la aplicación local o remota; no se reinician servicios.
El archivo no cifra las imágenes ni autentica el manifest: conservarlo privado y
cifrarlo antes de trasladarlo fuera del equipo. No sustituye un backup integral.

Siguiente tarea: revisar y concretar el ensayo operativo coordinado de DB y
objetos en un destino privado separado, antes de HTTPS/despliegue. No se declara
probada la restauración real de Contabo. La aceptación manual de inventario y
permisos continúa a cargo del propietario.
