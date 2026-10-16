# Entrega: frontend y proxy (fundación 1.3)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/foundation-frontend`. Hito 1 pendiente de CI.

## Resultado

- Next.js 16.4.2 App Router, React 19.3.1 y TypeScript 5.9.3 estricto.
- Node.js 24 en imagen oficial por digest; runtime verificado v24.22.0.
- Pantalla inicial en español, sin operaciones ni cifras comerciales simuladas.
- Página no encontrada y límite de errores con reintento, también en español.
- CSS responsive y controles semánticos, enlace de salto y foco visible.
- Cliente GET centralizado con `/api` relativo, credenciales same-origin,
  decodificador obligatorio y errores sin reflejar mensajes internos del servidor.
- Proxy Nginx no root, con filesystem de solo lectura y temporales en tmpfs.
- Único puerto al host: proxy en 127.0.0.1:8080. API/frontend/DB sin puertos propios.
- Sin variables públicas de URL API ni necesidad de fijar dominio al build.
- Salida Next standalone y lock npm con integridades; sin recursos visuales remotos.

## Archivos cambiados

- `.gitignore`, `compose.yaml`, `compose.dev.yaml`, `README.md`.
- `frontend/package.json`, `package-lock.json`, `.npmrc`, `Dockerfile`,
  `.dockerignore`, `.prettierrc.json`, `.prettierignore`.
- `frontend/tsconfig.json`, `next.config.ts`, `eslint.config.mjs`, `vitest.config.ts`.
- `frontend/src/app/layout.tsx`, `page.tsx`, `globals.css`, `not-found.tsx`, `error.tsx`.
- `frontend/src/lib/api/client.ts`.
- `frontend/tests/setup.ts`, `pages.test.tsx`, `api.test.ts`.
- `ops/proxy/nginx.conf`, `scripts/smoke_web.py`.
- `docs/decisions.md`, `docs/acceptance.md` y este registro.
- `next-env.d.ts`, `.next`, `node_modules` y cachés son generados/ignorados.
  La captura de la revisión visual se guarda fuera del repositorio.

## Comandos exactos ejecutados

Construcción y controles en Docker:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build frontend frontend-tests
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm frontend-tests
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait proxy
python3 scripts/smoke_web.py --base-url http://127.0.0.1:8080
```

Regresión backend en base aislada y limpieza posterior:

```bash
docker compose --project-name carrillo-test --env-file .local/test/compose.env -f compose.yaml -f compose.test.yaml run --rm tests
docker compose --project-name carrillo-test --env-file .local/test/compose.env -f compose.yaml -f compose.test.yaml down
```

Comprobaciones de runtime:

```bash
docker inspect carrillo-dev-db-1 carrillo-dev-backend-1 carrillo-dev-frontend-1 carrillo-dev-proxy-1 --format '{{.Name}} user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} ports={{json .HostConfig.PortBindings}}'
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T frontend node --version
```

También se ejecutaron pruebas, formato y typecheck locales con el Node 24
incluido en Codex; el Node del sistema es 20 y no satisface el engine del proyecto.
Los comandos locales portables con Node 24 están en README. Se consultó el audit
de ejecución mediante npm bajo Node 24:

```bash
PATH=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm audit --prefix frontend --omit=dev --audit-level=high --cache /tmp/taller-carrillo-npm-cache
```

## Resultados reales

- Build optimizado Next.js completado dentro de Docker, con TypeScript y páginas
  `/` y `/_not-found` prerenderizadas; imagen standalone construida.
- **15 pruebas frontend aprobadas**: 12 de cliente API y tres de pantallas.
- ESLint 10, Prettier y generación/comprobación de tipos correctos en Docker.
- Árbol npm limpio comprobado dentro de instalación Docker nueva: sin peers
  inválidos ni dependencias sobrantes.
- Audit de dependencias de ejecución: **found 0 vulnerabilities** en esta sesión.
  Es el resultado del catálogo consultado, no una garantía de ausencia de fallos.
- Regresión backend: **36 passed in 0.50s**, incluidas cinco de PostgreSQL real.
- Smoke HTTP: HTML español, **nueve assets** servidos, API del mismo origen,
  errores 404 de interfaz/API y cabecera `nosniff` verificados.
- Browser real: página renderizada, texto/estructura accesibles revisados;
  Tab enfoca `Ir al contenido` y Enter mueve el foco a `contenido`.
- API/frontend/proxy saludables. API sin publicaciones; DB sin publicaciones;
  frontend sin publicaciones; proxy publica solo loopback:8080.
- Backend UID 10001, frontend usuario node y proxy UID 101; esos tres servicios
  ejecutan con filesystem de solo lectura.

## Incidencias resueltas

- ESLint 9 emitió aviso de fin de soporte. ESLint 10 con el preset completo de
  Next arrastraba plugins con peers incompatibles. Se sustituyó por reglas
  oficiales Next, TypeScript y React Hooks compatibles; lint y árbol npm pasaron.
- Config Vitest ESM bajo package CommonJS emitía aviso. Se adoptó `type: module`
  y se repitieron pruebas, build y arranque standalone correctamente.
- Next añadió tipos de rutas de desarrollo al tsconfig. Se incorporó esa
  configuración y se normalizó el formato antes del build final.
- Nginx intentó crear `fastcgi_temp` en filesystem de solo lectura. Todos sus
  directorios temporales se movieron a tmpfs, manteniendo las restricciones.
- npm 9 del host emitió aviso de su propia API `url.parse`; no procede del cliente
  escrito. Los contenedores usan npm 11.19.1 e instalación limpia comprobada.

## Riesgos pendientes y siguiente tarea

- Sin login, CSRF, permisos, tenants ni funciones comerciales todavía. El cliente
  HTTP es solo lectura; mutaciones requieren la fase de acceso.
- HTTP limitado al host local, sin HTTPS ni políticas completas de producción.
- CSS móvil implementado; no se realizó inspección visual de viewport móvil o
  dispositivos físicos en esta sesión. Tampoco matriz de navegadores.
- Compose usa build optimizado sin hot reload. No sustituye un despliegue público.
- CI aún no existe; controles ejecutados manualmente y registrados aquí.
- Cambios registrados en commit local; sin push remoto.

Siguiente tarea: fundación 1.4, CI de lint, pruebas, builds e integración, y
verificación reproducible del conjunto para cerrar el hito 1.
