# Inventario 3.11 — Pantalla privada y conteos

`/inventario`: ficha con existencia, costo promedio, precio de venta, valoración e
historial. Conteo con identidad, revisión y resolución idempotente de respuestas
inciertas. Imagen real pendiente. Contrato en `../inventory-ui-contract.md`.

## Archivos

- `frontend/src/app/inventario/page.tsx`.
- `frontend/src/components/inventory-workspace.tsx`, `inventory-manager.tsx`:
  sesión, fichas/páginas, conteos y pendiente temporal por contexto.
- `frontend/src/lib/inventory/api.ts`: DTO, decimales exactos y operación.
- `frontend/src/lib/api/client.ts`: cabecera idempotente tipada; sin modificar
  semántica de altas/PATCH existentes ni añadir reintentos.
- `frontend/src/components/access-panel.tsx`, `frontend/src/app/globals.css`:
  enlace admin y estilos adaptables.
- `frontend/tests/inventory-api.test.ts`, `inventory-workspace.test.tsx`,
  `inventory-data.ts`: pruebas y datos sintéticos.
- `backend/tests/integration/browser_fixture.py`: limpieza de stock sintético
  antes de productos/empresas, exclusivamente en DB efímera protegida.
- README, contratos/modelo/permisos, esta entrega y capturas JPEG de datos sintéticos
  `inventory-3.11-desktop.jpg` (959×1677), `inventory-3.11-mobile.jpg` (375×2408).

## Comandos ejecutados

Desde `frontend/`, Node 24 del runtime disponible:

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
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Validación local: 229 pruebas frontend aprobadas, tipos y ESLint correctos. La
primera ejecución de ESLint señaló lectura de ref durante render; se corrigió
pasando el pendiente mediante el estado autorizado antes de validar.

CI integral final con código 0: 1.595 pruebas backend, 229 frontend y 9 por
Nginx/Uvicorn real aprobadas; workflow, lint, formato, tipos, builds, migraciones y
smoke correctos. Log `.local/validation/inventory-3.11-ci.log`. Cuatro imágenes
runtime con cero hallazgos de cualquier severidad y SBOM privado en
`.local/image-reports/bfe463fad9af48a291150a8e75753eb2/`. Recursos limpiados.
Secretos worktree/historial y locks Python sin hallazgos; npm conserva cuatro avisos
dev-only aceptados por SEC-2026-001 hasta 2026-10-31 00:00 UTC, sin ampliar excepción.
Reporte `.local/security-reports/bdde3b9c5ed04c61b68b2868d4383e87.json`.
Ruff local sin incidencias, 193 archivos con formato correcto y diff sin errores.
Los 52 tests de scripts incluyen seis de los cinco archivos experimentales heredados
sin seguimiento; 46 pertenecen al repositorio seguido. No se incluyen esos archivos
de respaldo en el commit de esta entrega.

## Navegador real

Instancia `carrillo-ci-580845ea1515`, proxy de loopback efímero; frontend reconstruido
con el código final antes del recorrido. Login contraseña/TOTP, empresa sintética A,
creación de producto y conteo físico con identidad/revisión. Resultado visible:
cantidad 2,5, costo promedio 3,25 USD, precio de venta 12,34 USD, valoración interna
8,125 USD e historial del conteo. Sin manipular estado interno del navegador.

Móvil solicitado 390×844: ancho cliente/scrollWidth ambos 375, sin desbordamiento.
Consola warn/error vacía. Capturas nativas completas JPEG, sin edición; viewport
restablecido, pestaña cerrada y copia privada de credenciales sintéticas eliminada.
Fixture finalizado código 0, una prueba de soporte aprobada y recursos limpiados.
Esa prueba mantiene el entorno; las acciones UI se verificaron con navegador, no
son asserts pytest. Logs locales: `.local/validation/inventory-3.11-browser.log`
y `inventory-3.11-browser-rebuild.log`. `docker cp` no leyó el archivo de tmpfs;
se confirmó fixture vivo y se copió con salida redirigida a archivo 0600, sin
imprimir credenciales ni reiniciar la base.

## Límites y siguiente tarea

Sin migraciones nuevas ni cambios a desarrollo/VPS; requiere backend 3.10 y sus
migraciones. Solo admin, sin conceder consultas a otros perfiles. Pendiente en memoria
temporal: cerrar/recargar exige revisar historial. Red incierta/cambio de empresa se
prueban como componentes con API simulada; idempotencia/rollback/concurrencia reales
siguen cubiertas en PostgreSQL y HTTP. No se declara una prueba manual de corte de red.

Siguiente tarea: imágenes de producto con adaptador privado de Contabo, sin ligar
cargas a transacciones de stock ni publicar archivos por defecto. Publicación pública,
compras y otros orígenes continúan pendientes. Despliegue externo sigue aplazado.
