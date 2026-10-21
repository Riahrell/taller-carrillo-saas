# Código de correo alfanumérico

Decisión posterior del propietario: seis caracteres alfanuméricos.
Se generan con secrets.choice sobre ABCDEFGHJKLMNPQRSTUVWXYZ23456789:
no hay 0, O, 1 ni I. Entrada insensible a mayúsculas/minúsculas.
Se conservan HMAC, cifrado de cola, consumo único, diez minutos desde contraseña,
cinco errores, cuotas de envío y permiso de navegador fijo de quince días.
Los códigos numéricos de intentos todavía vigentes siguen verificables hasta su
caducidad; los nuevos mensajes usan el generador alfanumérico.
No hay migración ni cambio de secretos.

Archivos completos:
- backend/app/modules/access/email_login.py
- frontend/src/components/email-login-flow.tsx
- docs/email-login-policy.md
- este registro

Validación ejecutada: Ruff, formato, TypeScript, ESLint y diff correctos.
Compilación Docker registrada en .local/validation/email-alphanumeric-build.log.
No se ejecutaron pruebas funcionales ni envíos desde el agente.

Desde la raíz:
```bash
.venv/bin/ruff check backend/app/modules/access/email_login.py
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
cd ..
git diff --check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --wait
```

Prueba manual: recargar e iniciar sesión en ventana privada; solicitar un correo
nuevo y comprobar que tenga seis caracteres, sin los ambiguos citados. Introducir
el código con letras en minúsculas: debe aceptar el mismo código. No compartirlo.
En seis elecciones aleatorias puede aparecer un código solo de letras o solo
de números; el alfabeto permite ambos, sin imponer una mezcla artificial.
En navegador previamente recordado no se solicita correo hasta vencer/revocar su
permiso. No acortar el plazo ni borrar permisos de otros usuarios para probar.

La revisión de imágenes se entrega en catalog-image-review-3.22.md.
Pendiente: validación manual del propietario y regresión del nuevo acceso.
