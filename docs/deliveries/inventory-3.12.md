# Inventario 3.12 — Preparación segura de imágenes

Procesador local de JPEG/PNG/WebP estáticos para la futura imagen principal.
Valida bytes, estructura, decodificación, tamaño y dimensiones; aplica orientación,
reduce proporcionalmente y genera PNG sin metadatos con hash SHA-256.
Conserva transparencia. No añade endpoints, persistencia, subida ni cambios UI.

## Archivos

- `backend/app/modules/catalog/images.py`: procesador y resultado inmutable.
- `backend/tests/test_product_images.py`: 29 pruebas con imágenes sintéticas reales.
- `backend/pyproject.toml`, `requirements.lock`, `requirements-dev.lock`:
  Pillow 13.0.0 con hashes.
- `docs/product-images-contract.md`, README y esta entrega: límites, configuración
  comunicada por el propietario y alcance pendiente.

## Validación

Desde la raíz del repositorio:

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_product_images.py -q
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
python3 -m scripts.ci
git diff --check
```

Resultado local ejecutado: 29 pruebas aprobadas, Ruff sin incidencias y 190
archivos con formato correcto. CI integral finalizó con código 0: 1624 pruebas
backend, 229 frontend y 9 de proxy real aprobadas; workflow, tipos, builds,
migraciones, smoke y escaneos runtime/SBOM correctos. Recursos efímeros limpiados.
Log: `.local/validation/images-3.12-ci.log`. Escaneos de secretos y locks Python
sin hallazgos. Persisten cuatro avisos npm exclusivos de desarrollo aceptados
por SEC-2026-001 hasta 2026-10-31 00:00 UTC; no se amplió la excepción.
Reporte: `.local/security-reports/e161ab04f8f5452cb0617f495edf899b.json`.

## Límites y siguiente tarea

8 MiB de entrada; 20 millones de píxeles; lado de entrada máximo 8192; salida
PNG RGBA de hasta 1600 por lado y 12 MiB. No aumenta imágenes pequeñas. Rechaza
SVG, GIF, BMP, TIFF, animaciones y archivos inválidos/truncados. Las credenciales
externas no se han leído. Ninguna operación en Contabo ni en la VPS.

Pendiente antes de habilitar cargas: worker con límites efectivos de memoria y
tiempo, concurrencia y cuotas; adaptador privado S3 con timeouts; autorización,
asociación transaccional y recuperación de objetos huérfanos; prueba real aislada.
La siguiente tarea es el worker aislado, después el adaptador Contabo configurado
externamente. La pantalla conserva el marcador de imagen pendiente hasta completar
el recorrido autorizado; este commit no declara imágenes operativas.
