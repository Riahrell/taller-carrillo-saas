# Continuidad al cambiar de pestaña

Se conserva el paso de acceso/MFA y los borradores de catálogo, inventario y
membresías al ocultar la pestaña. Las áreas privadas quedan ocultas e inertes
hasta comprobar al regresar el mismo usuario, empresa, membresía y perfil.
Cambio o pérdida de sesión retira la pantalla. No se guardan borradores en Storage.

Contraseñas y códigos escritos se limpian al ocultar. La clave/QR de incorporación
MFA y códigos de recuperación mantienen su política anterior: se retiran y puede
ser necesario reiniciar ese paso. No se reenvían escrituras automáticamente.
Una escritura interrumpida conserva el tratamiento de resultado incierto.
La comprobación al volver no renueva la confirmación de identidad.

## Archivos completos en el repositorio

- frontend/src/components/access-panel.tsx
- frontend/src/components/catalog-workspace.tsx
- frontend/src/components/inventory-workspace.tsx
- frontend/src/components/membership-workspace.tsx
- frontend/src/components/identity-confirmation.tsx
- frontend/src/components/membership-creation.tsx
- frontend/src/lib/access/same-context.ts
- frontend/tests/access-panel.test.tsx
- frontend/tests/catalog-workspace.test.tsx
- frontend/tests/inventory-workspace.test.tsx
- frontend/tests/membership-creation.test.tsx
- frontend/tests/membership-workspace.test.tsx

## Validación ejecutada

Desde la raíz:

```bash
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
"$NODE" node_modules/vitest/vitest.mjs run
cd ..
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --wait
python3 scripts/smoke_web.py --base-url http://localhost:8080
git diff --check
```

Resultados: TypeScript, ESLint y compilación Next correctos; 247 pruebas frontend
en 18 archivos aprobadas. Smoke HTTP correcto (HTML, diez assets, API, 404,
cabeceras y rechazo CSRF). Todos los servicios locales saludables tras levantar
los que estaban detenidos. Interfaz de acceso abierta en navegador real.
La continuidad se verificó mediante eventos simulados en las pruebas; no se
afirma una prueba manual completa de cambio de ventana en dispositivo real.
No se repitió la batería backend: no cambió su código.

Regresiones cubiertas: correo conservado y contraseña retirada, paso de correo MFA
sin nuevo inicio ni envío, borrador de catálogo conservado y descartado ante otra
empresa, cantidad/motivo de inventario sin escritura, contraseñas del alta retiradas
sin desmontar el formulario, plazo de confirmación sin renovación al recuperar foco.

## Límites y siguiente tarea

Los borradores solo duran mientras la página permanezca cargada; una recarga,
cierre o descarte de memoria del navegador los pierde. Se conserva el vencimiento
y la autorización del servidor. VPS y despliegue pendientes, sin cambios.
Siguiente tarea funcional: interfaz privada para proveedores, compras y recepción,
sobre la API existente, sin añadir reglas de impuestos ni pagos.
