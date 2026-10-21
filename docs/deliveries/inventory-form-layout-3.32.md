# Inventario 3.32: formularios y acciones

Mejora visual de entradas, salidas, conteos y fotos. No cambia reglas de stock,
costos, autorización, contratos HTTP ni procesamiento de imágenes.

- Etiquetas en bloque, campos de ancho completo, tipografía de 16 px, controles
  de al menos 48 px, bordes redondeados y ayudas separadas.
- Acciones principales y cancelación en una barra flexible, con separación y
  botones secundarios diferenciados. El texto puede ocupar varias líneas en móvil.
- Botones de envío asociados a su formulario mediante `form` e ID de React;
  conservan envío con Enter, validación nativa y manejadores existentes.
- Cámara y galería en tarjetas independientes, con selector nativo de archivo
  estilizado y etiquetas accesibles. No se ocultan controles ni cambia capture/accept.
- Al abrir el editor de fotos se usa todo el ancho; el nombre del producto ya no
  queda comprimido en una columna estrecha al lado de los controles de carga.

## Archivos completos

- `frontend/src/app/globals.css`
- `frontend/src/components/inventory-manager.tsx`
- `frontend/src/components/product-image.tsx`
- Este registro.

## Validación ejecutada

274 pruebas frontend correctas en 19 archivos en la ejecución final, incluidos
movimientos, fotos, reintentos y cancelación. TypeScript, ESLint y Prettier correctos.
La primera corrida completa tuvo un fallo de foco en access-panel, fuera de los
componentes modificados; ese archivo pasó aislado (21 pruebas) y la siguiente
corrida completa pasó. No se modificó esa prueba ni la autenticación.

Se midió el DOM de una vista previa HTML de ejemplo que usa la misma hoja de
estilos, en anchos de 1280 y 390 px. En la comprobación móvil final los pares de
botones comparten coordenada vertical y no hay desbordamiento horizontal. El
navegador no permitió capturar imagen; no se declara revisión visual autenticada
ni prueba en móvil físico. La vista previa y su servidor temporal se cerraron.

Registros privados: `.local/validation/inventory-form-layout-tests-final.log`,
`inventory-form-access-focus.log` y `inventory-form-layout-build.log`.
No se ejecutaron pruebas backend porque no cambia esa capa ni sus contratos.
No se crearon productos, movimientos o fotos operacionales.

Desde frontend, con Node 24:

```bash
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/vitest/vitest.mjs run
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
"$NODE" node_modules/prettier/bin/prettier.cjs --check src/components/inventory-manager.tsx src/components/product-image.tsx src/app/globals.css
```

Desde la raíz:

```bash
git diff --check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
```

## Revisar manualmente

Abrir Registrar entrada/salida o Actualizar existencias: comprobar separación de
etiquetas y ayudas, campos amplios y botones Revisar/Cancelar en una fila cuando
el ancho lo permite. En revisión, verificar Confirmar/Corregir/Cancelar.
Abrir Cambiar foto: cámara y galería separadas; Continuar y Cancelar juntos y
nombre del producto sin comprimirse. Probar Tab/Enter y ancho de celular.
Cancelar debe conservar existencias o foto actual; confirmar sigue requiriendo la
revisión anterior. No se guarda una foto solo por elegir un archivo.

Siguiente tarea del itinerario: historial unificado de inventario. Esta mejora
visual no cierra los pendientes funcionales ni despliega en la VPS.

Actualización local ejecutada: frontend saludable y smoke HTTP correcto en
`http://localhost:8080`. Comando adicional:

```bash
python3 scripts/smoke_web.py --base-url http://localhost:8080
```
