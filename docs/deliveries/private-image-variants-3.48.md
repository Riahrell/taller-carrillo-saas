# 3.48 — Miniaturas y WebP privados

Fotos nuevas/reemplazadas: detalle WebP hasta 1600 px y miniatura hasta 300 px,
sin metadatos privados y preservando transparencia/orientación. Las listas usan
la miniatura y conservan carga diferida. El catálogo mantiene páginas de 25
productos, máximo API 100 por solicitud; no carga todos los productos a la vez.

Las fotos PNG antiguas siguen funcionando; no se convierten ni eliminan en bloque.
Se optimizan al reemplazarlas. Sin CDN pública, caché compartida o cambios de
permisos. Se mantienen bucket privado, integridad y reautorización posterior a S3.

## Archivos cambiados

- Backend catálogo: procesamiento acotado, sobre del subprocess, parser WebP,
  transporte S3 tipado PNG/WebP, reserva y metadatos de las dos variantes,
  publicación después de ambos PUT, limpieza recuperable y rutas de miniatura.
- `backend/app/modules/inventory/routes.py`: miniatura operativa con la guardia
  existente. DTO de recibo público conserva sus campos anteriores.
- Migración `0047_product_image_variants.py` y modelo de imágenes; lectura PNG
  compatible y finalización de reservas PNG pendientes del despliegue anterior.
- Frontend: `product-thumbnail.tsx`, cliente `lib/catalog/images.ts`, prueba del
  componente y nuevo `image-variants-api.test.ts`.
- Pruebas backend de codec/transporte, variantes/errores/compatibilidad, adaptación
  de fixtures de dos objetos y expectativas de head de Alembic.
- Contrato de variantes, actualización del contrato histórico, permisos y plan.

## Validación ejecutada

Desde la raíz:

```bash
python3 -m scripts.validate_identity --test-path tests/test_product_images.py --test-path tests/test_product_variants.py --test-path tests/test_image_processing.py --test-path tests/test_image_storage.py --test-path tests/integration/test_product_image_variants.py --test-path tests/integration/test_product_image_service.py --test-path tests/integration/test_product_image_http.py --test-path tests/integration/test_product_image_reconciliation.py --test-path tests/integration/test_inventory_permissions.py --maxfail 3
python3 -m scripts.security
git diff --check
```

Resultado: 113 pruebas backend aprobadas; Alembic sin operaciones nuevas pendientes.
Incluyen transparencia, orientación, ausencia de metadatos, dimensiones, firmas,
límites de worker, integridad MIME/hash, cuota combinada, fallo de segunda subida,
reintentos, concurrencia, borrado parcial, PNG antiguo y reserva PNG incierta,
privacidad de miniaturas y revocación operativa antes/después de almacenamiento.

La primera migración de prueba detectó un nombre de restricción duplicado por
la convención de Alembic; corregido antes de aplicar localmente. Dos pruebas de
restricciones usaban la extensión PNG al copiar una fila WebP: se ajustó la
representación base, manteniendo sus verificaciones de rechazo de metadatos.

Desde `frontend`, con Node 24 en PATH:

```bash
npm test
npm run typecheck
npm run lint
```

Resultado: 343 pruebas aprobadas en 28 archivos, tipos y ESLint correctos.
La validación Docker final aprobó también Prettier, tipos, lint y las mismas
343 pruebas con las versiones fijadas por CI.

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend frontend-tests
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm --no-deps frontend-tests
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend image-worker
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Build backend/frontend aprobado. Migración local confirmada en
`0047_product_image_variants (head)`; backend, frontend y worker de imágenes
actualizados. Smoke HTTP aprobado. Logs privados ignorados en
`.local/validation/image-variants-*`. No se ejecutó el workflow remoto completo,
ni una prueba manual autenticada, ni se subieron fotos reales a Contabo durante
los tests: transporte SDK verificado con Stubber y ciclo transaccional con
almacenamiento simulado y PostgreSQL real aislado.

## Prueba manual

1. Recarga la web. Como administrador, abre un producto y reemplaza su foto.
2. Confirma que el detalle y la miniatura del catálogo muestran la nueva imagen
   con orientación correcta. Repite con cámara/galería si quieres comprobar móvil.
3. Abre la consulta con un empleado autorizado: debe ver la miniatura; retirar
   su permiso y actualizar debe bloquear el acceso.
4. Una foto anterior PNG debe seguir visible. Al reemplazarla usará WebP.
5. Con más de 25 productos, usa Más productos y vuelve al inicio; aplica búsqueda
   y categoría. La optimización de fotos no cambia la paginación.

## Riesgos y siguiente tarea

- Los tamaños finales dependen de cada imagen; no se prometen ratios o tiempos
  medidos en fotos reales. No se guardan originales sin transformar.
- PNG anteriores conservan su tamaño hasta reemplazo. Una migración masiva exige
  otra tarea con cuotas, comparación del puntero vigente y evidencia.
- Continúan los cuatro hallazgos npm de desarrollo bajo SEC-2026-001; los locks
  Python y detectores de secretos no reportaron hallazgos nuevos.
- Se mantiene la política privada no-store. La revocación no borra copias que un
  usuario autorizado ya haya guardado. No se desplegó en VPS.

Siguiente tarea: aceptación manual de catálogo/fotos e inventario; después cerrar
los pendientes operativos y de seguridad antes del despliegue.
