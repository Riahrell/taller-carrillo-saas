# Textos claros para actualizar productos

Se aplicó la terminología propuesta por el propietario, conservando campos,
información, permisos, cálculos y la revisión antes de guardar.

- Registrar conteo / Conteo físico → Actualizar existencias.
- Cantidad contada → Existencias reales en bodega.
- Ayuda: «Escribe cuántas unidades hay ahora. Escribe 0 si no hay.»
  Se conserva la precisión de hasta tres decimales.
- Motivo → ¿Por qué cambian las existencias?
- Revisar conteo → Actualizar inventario; se avisa que el paso siguiente
  revisa los datos. El guardado sigue requiriendo Confirmar actualización.
- Editar conteo → Corregir datos.
- Cancelar → Cancelar, no cambiar nada, solo mientras no hay resultado incierto.
  Si existe un pendiente, se muestra Volver al producto, sin prometer reversión.
- Historial de conteos → Historial de actualizaciones; indica su alcance real
  de ajustes de existencias, sin presentar compras o ventas como incluidas.
- Costo unitario declarado → Costo por unidad, con ayuda en lenguaje cotidiano.
- Cambiar imagen → Cambiar foto del producto.
- Foto: elegir desde galería, revisar, guardar y cancelar conservando la actual.

Archivos completos: frontend/src/components/inventory-manager.tsx,
frontend/src/components/product-image.tsx, y expectativas de textos en
frontend/tests/inventory-workspace.test.tsx y product-image.test.tsx.
No se cambió la lógica de pruebas ni se agregaron pruebas nuevas.

Comandos desde raíz:
```bash
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
cd ..
git diff --check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
```

Resultados: formato, TypeScript y ESLint correctos; build en
.local/validation/inventory-copy-build.log. No se ejecutaron pruebas funcionales
ni se cambiaron productos, existencias o fotos desde el agente.
La adaptación anterior del acceso sigue pendiente de regresión automatizada.

Prueba manual: recargar localhost:8080, abrir un producto en Inventario y
Actualizar existencias; revisar textos y cancelar sin guardar. Revisar la pantalla
de confirmación sin pulsar Confirmar actualización si no se quiere cambiar stock.
Abrir Cambiar foto del producto y cancelar para conservar la actual.
Siguiente tarea: recoger comentarios de uso y aplicar el mismo criterio a las
pantallas que el propietario identifique.
