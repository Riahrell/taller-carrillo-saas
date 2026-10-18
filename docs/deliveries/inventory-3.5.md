# Inventario 3.5 — Pantalla del catálogo

Interfaz `/catalogo` para consultar y crear productos/categorías de la empresa de
sesión. Revisión antes de enviar, confirmación de identidad reutilizable sin
prolongarla, precios exactos como texto y comprobación del resultado/contexto.
Contrato completo en `../catalog-ui-contract.md`.

## Archivos

- `frontend/src/app/catalogo/page.tsx`.
- `frontend/src/components/catalog-workspace.tsx`, `catalog-manager.tsx`.
- `frontend/src/lib/catalog/api.ts`: DTO estrictos, contexto y precio decimal.
- `frontend/src/components/access-panel.tsx`: enlace solo para admin.
- `frontend/src/components/identity-confirmation.tsx`: propósito textual opcional;
  mantiene verificaciones/ventanas previas.
- `frontend/src/app/globals.css`: catálogo responsive, sin estilos inline.
- `frontend/tests/catalog-api.test.ts`, `catalog-workspace.test.tsx`.
- `backend/tests/integration/browser_fixture.py`: teardown de registros sintéticos
  del catálogo antes de retirar sus empresas; no se incluye en la imagen runtime.
- `ops/proxy/Dockerfile`: actualización fijada de libx11/libxpm tras nuevos avisos
  del escáner; seguimiento en `../container-security.md`.
- Contrato, README, esta entrega y capturas con datos sintéticos
  `inventory-3.5-catalog.jpg`, `inventory-3.5-mobile.jpg`.

## Comandos ejecutados

Validación local con Node v24.19.0 del runtime disponible, desde `frontend/`:

```bash
/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/typescript/bin/tsc --noEmit
/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/eslint/bin/eslint.js .
/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/vitest/vitest.mjs run
```

Desde la raíz:

```bash
python3 -m scripts.browser_qa
python3 -m scripts.ci
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
git diff --check
```

32 pruebas enfocadas iniciales y 188 del frontend completo pasaron; se añadió
después la regresión de reutilización de confirmación (13 de workspace pasan).
CI verifica la versión final y sus recuentos se registran al terminar.
Tipo/lint local pasan tras corregir el estrechamiento TypeScript del producto y
una opción inválida del selector de pruebas, sin omitir esas comprobaciones.

El primer CI conjunto pasó pruebas, builds y smoke, pero terminó con código 1 al
detectar cinco hallazgos nuevos en el proxy (uno HIGH): libx11 1.8.13-r0 y libxpm
3.5.19-r0, con base de vulnerabilidades actualizada a 2026-10-18T18:32:29Z.
No se tomó como verde ni se amplió excepción. Se fijaron versiones corregidas
1.8.14-r0 y 3.5.20-r0 y se repitió CI integral. Evidencia original privada:
`.local/image-reports/12a2d004d89c45ae862353b315ee3f1b/` y
`.local/validation/catalog-3.5-ci.log`; repetición en `catalog-3.5-ci-fixed.log`.

Resultado final: **CI código 0**, 1453 pruebas backend, 189 frontend, 8 por proxy
real y 52 scripts del worktree (incluyen herramientas de respaldo aún no
versionadas). Lint, formato, tipos, builds, migraciones, workflow y smoke pasan.
Las cuatro imágenes runtime tienen **0 hallazgos**, con informes/SBOM privados en
`.local/image-reports/d071e337ae144ae4b3e30cf802eb4a66/`. Los cuatro avisos npm
dev-only de SEC-2026-001 siguen aceptados temporalmente, sin extender el plazo.
No sumar pruebas de ejecuciones repetidas como casos distintos. CI retiró sus
recursos y `git diff --check` pasa.

## Navegador real

Primera sesión temporal interrumpida: proceso ausente y proxy/backend/DB
confirmados detenidos; no se tomó como éxito final. Se retiraron únicamente sus
contenedores/volúmenes de prueba y se preparó otro entorno, sin tocar desarrollo
ni VPS. La sesión final completó login con TOTP, selección de empresa, categoría
nueva y producto de prueba con precio «12,34» registrado/revisado como «12.34».
La segunda alta reutilizó la confirmación vigente sin pedir otra contraseña.

Ancho móvil solicitado 390 px: listado y revisión midieron scrollWidth = clientWidth
= 375 px (descontada la barra vertical), sin desbordamiento horizontal. Se revisó
precio límite NUMERIC(20,2) y SKU de 100 caracteres; **se canceló sin guardar** esa
segunda operación. Captura móvil de viewport tras fallar la captura full-page.
Consola sin errores/advertencias. Viewport restaurado y pestaña cerrada.

Fixture de apoyo final terminó con código 0, **1 prueba pasa**, y retiró su entorno;
las verificaciones de interfaz se realizaron con el navegador, no dentro de pytest.
Credenciales aleatorias en tmpfs/archivo privado, nunca stdout; copia local retirada.
Las capturas solo contienen datos sintéticos y no prueban aceptación en teléfono
físico ni HTTPS público. Logs privados: `.local/validation/catalog-3.5-*.log`.
El parche de paquetes del proxy es posterior a este recorrido visual; su runtime
se verifica en la repetición integral con Nginx real. No modifica la interfaz.

## Pendientes

Sin migraciones ni arranque/mutación del entorno de desarrollo en esta entrega.
No inventario completo, edición, desactivación o búsqueda. No idempotencia general
de altas: ante resultado incierto revisar, nunca reenviar automáticamente.
VPS/TLS/restauración remota siguen pospuestos. SEC-2026-001 conserva vencimiento.
Herramientas previas de respaldo permanecen sin seguimiento y fuera de esta entrega;
no se declara el repositorio completo limpio ni cerrado el hito 3.

Siguiente tarea: modelo de movimientos y saldos de inventario con transacciones,
costeo, idempotencia y pruebas de concurrencia, antes de habilitar cambios de stock.
