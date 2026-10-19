# Inventario 3.15 — Imágenes privadas en inventario y catálogo

Carga con revisión, cámara/galería, lectura privada y reconciliación de objetos.
La miniatura del catálogo corresponde a la misma imagen del inventario.
No cambia precio, costo ni stock. Configuración S3 optativa y externa al repositorio.

## Archivos cambiados

- Backend: `catalog/image_configuration.py`, `image_reconciliation.py`,
  `image_routes.py`, `image_service.py`, `tools/image_worker.py`, configuración,
  lifespan y middleware de cuerpos binarios.
- Frontend: `lib/catalog/images.ts`, `product-image.tsx`, `product-thumbnail.tsx`,
  managers de catálogo/inventario, workspace, CSS y CSP para blobs privados.
- Operación: `compose.images.yaml`, proxy Nginx, aislamiento CI y browser QA.
- Pruebas HTTP, configuración, reconciliación, proxy, componentes y cliente HTTP.
- Contrato de imágenes y capturas desktop/mobile/catalog de esta entrega.

## Comandos de validación

Desde la raíz:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_product_image_http.py --test-path tests/integration/test_product_image_reconciliation.py --test-path tests/integration/test_product_image_service.py --test-path tests/test_image_configuration.py --maxfail 3
python3 -m scripts.ci
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Para repetir QA real, solo con configuración privada preparada y bucket autorizado:

```bash
python3 -m scripts.browser_qa --product-images-config /ruta/privada/product-images.env
```

El proceso crea un proyecto/base efímeros; guardar el diario de objetos sintéticos
antes de cerrarlo y eliminar únicamente esos objetos cuando no queden referencias.
No ejecutar este procedimiento contra datos de producción.

## Resultados observados

41 pruebas backend enfocadas aprobadas; 243 frontend aprobadas, TypeScript y ESLint
sin errores. Ruff correcto, 208 archivos formateados. Una primera CI detectó una
copia anterior sin formatear; se corrigió antes de repetir la corrida final.

Navegador real: subida con MFA, revisión y asociación en PostgreSQL temporal;
Contabo devolvió el PNG 640×420 con hash correcto y sin metadatos originales.
La misma imagen se vio en inventario y catálogo. Vista móvil sin desbordamiento
horizontal (375/375 px). Se destruyó el entorno efímero y Contabo confirmó la
eliminación del único objeto sintético creado. Credenciales externas al repositorio.

CI final aprobada (código 0): 1.691 backend, 243 frontend, 10 proxy y 46 pruebas
de scripts; lint, formato, TypeScript, builds, migraciones, smoke HTTP y escaneos
de imágenes/SBOM correctos. Log `.local/validation/images-3.15-ci-final.log`.
Las advertencias npm de desarrollo bajo SEC-2026-001 conservan su vencimiento
2026-10-31 00:00 UTC; no se amplió la excepción.

## Riesgos y siguiente tarea

La cámara física de un teléfono no se probó; se verificaron los controles y el
atributo capture en pruebas de componentes. El soporte depende del navegador.
HEIC no admitido. Revisar una carga real desde el teléfono del usuario.
El procesador tiene límites de recursos, sin aislamiento adicional de red/filesystem.
Activación operativa, restauración externa y HTTPS/VPS siguen pospuestos.
Siguiente tarea: revisión funcional del usuario y cierre de cualquier incidencia
antes del siguiente módulo. No se declara completa la seguridad de producción.
