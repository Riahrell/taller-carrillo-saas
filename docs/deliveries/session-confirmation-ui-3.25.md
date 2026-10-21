# Confirmación única por sesión y formularios accesibles

El propietario confirmó que quiere contraseña una vez al iniciar sesión, sin
repetir la pantalla al abrir módulos, y volver a iniciar al cerrar el navegador.

## Cambio implementado

Las sesiones del acceso por correo ya tienen una prueba consumida ligada a la
contraseña del login. El guard administrativo ahora acepta esa prueba durante
la sesión válida, dentro de la transacción: sigue resolviendo usuario activo,
revisión, membresía, rol, caducidad, inactividad y revocación. No se confía en un
flag del cliente. Se cambia explícitamente la política anterior de reconfirmar
cada cinco minutos para estas sesiones. Los flujos heredados conservan su guard.

GET /auth/security proporciona el contexto autorizado y segundos restantes para
que los componentes omitan el formulario repetido. El frontend verifica el mismo
usuario/empresa/membresía/rol y conserva un plazo conservador hasta la caducidad
absoluta. Imágenes y conteos ya no recortan ese plazo a cinco minutos.
No se alarga la duración de sesión ni la verificación de navegador de 15 días.

La cookie de sesión es HttpOnly, SameSite y Secure en producción, sin Expires ni
Max-Age. Para empezar a usarla, cerrar sesión una vez y volver a entrar. Las
cookies persistentes que ya existían no cambian retroactivamente.

**Límite real:** una cookie de sesión puede conservarse si el navegador restaura
sesiones o no termina sus procesos. Cerrar una pestaña no equivale a cerrar todo
el navegador. No se promete detectar cada cierre ni se usan unload/beacon para
una revocación que podría perderse. «Cerrar sesión» sigue siendo la forma
explícita de revocar inmediatamente. Cambiar pestaña o módulo conserva acceso.

## UI

Campos compartidos con padding y tamaño de letra de 16 px, etiquetas de 16 px,
ayudas de 14 px (16 en la confirmación), controles de al menos 48 px y foco
visible. Contraseña con botón Mostrar/Ocultar etiquetado y área de toque de
44 px. Ajuste de tarjeta para pantallas pequeñas. Las contraseñas visibles u
ocultas se vacían al ocultar la pestaña. La confirmación antigua queda como
fallback; el flujo por correo muestra solo una comprobación breve, sin pedir
otra contraseña. No se afirma una auditoría completa WCAG.

## Archivos completos

- backend/app/modules/access/http_security.py
- backend/app/modules/access/identity_routes.py
- backend/app/modules/access/reauthentication.py
- frontend/src/components/identity-confirmation.tsx
- frontend/src/components/inventory-manager.tsx
- frontend/src/components/product-image.tsx
- frontend/src/lib/access/identity.ts
- frontend/src/app/globals.css
- docs/email-login-policy.md
- este documento

## Validación y comandos

Ruff, TypeScript, ESLint, formato y diff correctos. Compilación Docker en
.local/validation/session-confirmation-build.log. No se ejecutaron pytest,
Vitest ni pruebas funcionales de acceso o inventario, según petición del usuario.
Los contratos automatizados de confirmación anterior deben adaptarse antes de
dar por cerrada la regresión. No se alteraron existencias, precios ni finanzas.

Desde la raíz:
```bash
.venv/bin/ruff check backend/app/modules/access/reauthentication.py backend/app/modules/access/http_security.py backend/app/modules/access/identity_routes.py
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
cd ..
git diff --check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --wait
```

## Prueba manual del propietario

1. Recargar, cerrar sesión y volver a entrar con contraseña. Código por correo
   solo si el navegador no está recordado o su permiso venció.
2. Catálogo → Nuevo producto: debe abrir los datos sin pedir otra contraseña.
3. Abrir imágenes, conteos y membresías: tampoco deben repetir la confirmación.
   No confirmar conteos de prueba sobre datos reales si solo se revisa la UI.
4. Esperar más de cinco minutos con actividad y volver al alta: no debe pedir
   reconfirmación por aquel plazo. Inactividad de 30 minutos aún vence la sesión.
5. Cambiar módulo o pestaña y regresar: conserva sesión y borradores existentes.
6. Revisar en celular o con zoom 200 %: campos, ayudas y botones legibles,
   navegación por Tab y foco visibles. No se validó un móvil físico.
7. Cerrar todas las ventanas y terminar el navegador, volver a abrirlo:
   debería pedir login si el navegador descarta sus cookies de sesión. Si
   restaura acceso, revisar su ajuste de restauración; usar Cerrar sesión para
   revocación garantizada. No borrar el permiso de correo de quince días.
8. Cerrar sesión en otra pestaña: debe retirarse el formulario y bloquear
   escrituras; no debe bastar con la confirmación previa del frontend.

Siguiente tarea: recoger resultados y ajustar los casos de regresión del contrato
de sesión, antes de considerar cerrada esta fase.
