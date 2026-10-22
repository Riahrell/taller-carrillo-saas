# 3.52 — Correspondencia entre archivo de imágenes y base restaurada

Se añade `check-database` a la herramienta administrativa de imágenes. Verifica
el archivo completo antes de consultar las referencias vigentes en
una transacción REPEATABLE READ de solo lectura y exige igualdad exacta por clave,
hash, tamaño, formato y dimensiones. Incluye miniaturas y detecta referencias
faltantes o adicionales. No usa S3 ni escribe en la base.

Archivos cambiados:

- backend/app/tools/image_archive.py
- backend/tests/integration/test_product_image_variants.py
- docs/operations/private-image-backup.md
- docs/inventory-release-plan.md
- docs/deliveries/image-archive-database-match-3.52.md

Comandos ejecutados desde raíz:

```sh
.venv/bin/ruff check backend/app/tools/image_archive.py backend/tests/integration/test_product_image_variants.py
.venv/bin/ruff format --check backend/app/tools/image_archive.py backend/tests/integration/test_product_image_variants.py
PYTHONPATH=backend .venv/bin/python -m app.tools.image_archive --help
python3 -m scripts.validate_identity --test-path tests/test_image_archive.py --test-path tests/integration/test_product_image_variants.py --maxfail 3
```

Resultados: 16 pruebas aprobadas en PostgreSQL temporal, Alembic check sin cambios,
Ruff correcto. Casos nuevos: coincidencia exacta, imagen sustituida después del
archivo, dimensión incorrecta y archivo sin entradas frente a una base con fotos.
La consulta no modifica las imágenes ni el inventario. Log privado ignorado:
.local/validation/image-archive-match-3.52.log.

No se repite frontend: no cambia la interfaz. No se modifican dependencias, esquema ni servicios locales.

Pendiente operativo confirmado por el propietario: no existe segundo bucket.
Se propone uno privado, taller-carrillo-restauracion-pruebas, sujeto a disponibilidad
y condiciones del proveedor. No se crea ni se cobra ningún recurso desde aquí.
El ensayo real necesita configuración de destino independiente y mantenimiento
coordinado de DB/objetos. Continúan pendientes la aceptación manual, ensayo real,
HTTPS y preparación revisable de VPS. Esta entrega no declara el sistema desplegado
ni recuperado desde Contabo.
