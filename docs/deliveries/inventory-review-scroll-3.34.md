# Entrega 3.34 — Desplazamiento y revisión de inventario

Cambios:
- Las comprobaciones de sesión conservan el espacio del contenido mientras lo ocultan y bloquean con `visibility`, `aria-hidden` e `inert`. Antes `hidden` retiraba ese espacio y podía llevar la página al inicio. Se aplica a inventario, catálogo, usuarios e inicio.
- El foco automático conserva la posición mediante `preventScroll`. Se mantienen las comprobaciones de sesión y de contexto al regresar a una pestaña.
- El acceso activo permanece montado durante la comprobación, oculto e inerte hasta obtener el resultado. Un resultado inválido reemplaza la vista como antes.
- Los botones dicen «Revisar entrada» o «Revisar salida». Son movimientos manuales: no registran ventas ni pagos.
- La confirmación presenta producto, unidades, motivo y costo en filas separadas, con columnas en escritorio y etiquetas encima del valor en móvil. Se corrigieron espacios entre textos de ayuda.

Archivos modificados: `frontend/src/app/globals.css`; componentes `access-panel`, `catalog-manager`, `catalog-workspace`, `identity-confirmation`, `inventory-manager`, `inventory-workspace`, `membership-creation`, `membership-manager`, `membership-workspace`; `frontend/tests/inventory-workspace.test.tsx`; este documento.

Validación ejecutada desde `frontend` usando Node del runtime local:

```bash
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js .
```

Resultado final: 274 pruebas aprobadas, TypeScript y ESLint sin errores. Se amplió la prueba de ocultación de inventario para comprobar que mantiene el contenedor sin `hidden`, con visibilidad oculta y sin acceso interactivo ni exposición accesible. Se actualizaron las expectativas de los botones de revisión. Una primera ejecución tuvo un fallo de foco en identidad; la ejecución final completa pasó. No se cambiaron las reglas de identidad para omitirlo.

Compilación Docker del frontend completada. Actualización local:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
```

Prueba manual pendiente: bajar hasta una ficha de inventario, abrir entrada/salida, pasar a revisión y volver de otra pestaña. Confirmar que conserva el desplazamiento y los datos del formulario. Revisar el resumen en escritorio y celular. No se ejecutó una prueba visual autenticada ni pruebas backend, dado que no cambiaron sus contratos ni reglas.

Riesgo pendiente: los navegadores pueden desplazar la página cuando se abre el teclado móvil o el contenido pasa a ser más corto que la posición actual; falta comprobarlo en un dispositivo real. El módulo de ventas sigue pendiente y no se representa como implementado. Siguiente tarea del itinerario: historial unificado de inventario.
