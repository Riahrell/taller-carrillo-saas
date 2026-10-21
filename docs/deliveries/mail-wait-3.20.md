# Avisos de espera de correo

Archivos completos: frontend/src/components/mfa-flow.tsx y
frontend/src/components/password-recovery.tsx.

La interfaz indica el mínimo de 60 segundos y que los límites de seguridad pueden
prolongar la espera. Incorporación MFA aclara que solo hay un envío por intento:
para otro se cancela el acceso pendiente y se inicia sesión de nuevo.
Recuperación conserva la respuesta genérica que no revela si existe una cuenta.
No cambia reglas del backend ni envía correos automáticamente.

Comandos desde la raíz:
```bash
git diff --check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
```

Validación: formato y diff correctos; compilación registrada en
.local/validation/mail-wait-build.log. No se ejecutó batería de pruebas,
según la preferencia del usuario. Prueba manual pendiente a cargo del usuario:
recargar la página, revisar el aviso en verificación por correo y en /recuperar,
y comprobar el mensaje tras una solicitud propia. No es un contador ni garantiza
entrega transcurridos 60 segundos. Siguiente tarea: interfaz de compras.
