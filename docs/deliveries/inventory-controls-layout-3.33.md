# Entrega 3.33 — Controles de inventario y fotos

Se agruparon los controles de foto y se aplicó el mismo diseño flexible a la paginación de productos, entradas/salidas e historial de actualizaciones: separación de 12 px, altura mínima de 48 px y texto adaptable. Los controles se colocan al lado cuando hay espacio y pasan a otra fila en pantallas estrechas. Se conservan sus acciones y restricciones existentes.

Archivos cambiados:
- `frontend/src/app/globals.css`
- `frontend/src/components/product-image.tsx`
- Este documento.

Validación ejecutada desde `frontend` usando Node del runtime local:

```bash
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js src/components/product-image.tsx
```

Resultados: 274 pruebas aprobadas; TypeScript y ESLint sin errores. Prettier aplicado y `git diff --check` sin errores. Compilación Docker del frontend completada.

Actualización local:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
```

Revisión manual pendiente: abrir inventario en escritorio y celular, revisar separación de las tres paginaciones y controles de foto, y comprobar foco visible con teclado. No se verificó visualmente una sesión autenticada. Este ajuste no resuelve fallos del servicio de imágenes.

Siguiente tarea del itinerario: historial unificado de inventario.
