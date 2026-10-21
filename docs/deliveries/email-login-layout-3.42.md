# Entrega 3.42 — Formulario de código de acceso

## Cambios

El componente no tenía la clase compartida identity-flow y aparecía con controles
nativos sin el formato de acceso. Ahora utiliza sus campos, botones, separación
y tipografía. El campo ocupa el ancho disponible y está asociado al texto de ayuda.
Enviar correo es una acción secundaria de ancho completo; Verificar y Cancelar
comparten fila cuando caben y se ajustan en móvil. Mensajes con formato accesible.
La lógica del código, caducidad, reenvío, sesión y cancelación se conserva.

Archivos completos en el repositorio:

- `frontend/src/components/email-login-flow.tsx`
- `frontend/src/app/globals.css`
- `docs/deliveries/email-login-layout-3.42.md`

## Comandos y resultados reales

Desde `frontend/`:

```bash
CARRILLO_NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$CARRILLO_NODE" node_modules/typescript/bin/tsc --noEmit
"$CARRILLO_NODE" node_modules/eslint/bin/eslint.js src/components/email-login-flow.tsx
"$CARRILLO_NODE" node_modules/vitest/vitest.mjs run tests/access-panel.test.tsx
```

TypeScript y ESLint correctos. 21 pruebas existentes del panel de acceso aprobadas.
No se agregaron pruebas que reproduzcan el CSS ni se ejecutó el backend completo.

Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

Compilación aprobada, frontend saludable y smoke HTTP aprobado. No se solicitaron
correos ni se cambiaron datos para verificar este ajuste visual.

## Revisión manual y pendiente

Volver a la pantalla de código y comprobar ancho del campo, separación y botones
en escritorio/móvil; navegar con Tab para verificar foco visible. La aceptación
visual autenticada corresponde al propietario: no se afirma inspección visual
sobre una sesión real ni entrega real de un correo en esta fase.
El siguiente bloque funcional sigue siendo permisos de inventario para empleados.
