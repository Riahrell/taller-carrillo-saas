# Arranque local después de 3.20

Web disponible en http://localhost:8080, solo loopback. Cuenta local conservada,
sin cambiar contraseña ni crear usuarios predeterminados. VPS intacta.

Base local detenida en 0024: respaldo privado en .local/development/backups,
índice del dump comprobado con pg_restore --list (no se afirma una restauración).
Migrada a 0036_purchase_receipts. No había factores MFA guardados; se generó la
clave local de identidad faltante sin sobrescribir ninguna clave existente.
Configuración externa existente de SMTP/Contabo montada mediante overlays;
no se copiaron credenciales a Git. El usuario completa el alta MFA desde la UI.

## Corrección necesaria

El worker de correo forzaba validación de origen production incluso en desarrollo,
por lo que rechazaba localhost HTTP y reiniciaba. IdentityMailDispatcher ahora
acepta el entorno validado de Settings, con production como valor predeterminado.
Solo development/test aceptan HTTP loopback conforme al validador existente;
production sigue exigiendo HTTPS, y un entorno desconocido se rechaza.

Archivos: backend/app/modules/access/identity_delivery.py,
backend/app/tools/identity_worker.py y backend/tests/test_identity_worker_origin.py.
Este documento registra operación y resultados. No se enviaron correos de prueba
adicionales desde el agente; el worker queda listo para solicitudes de la interfaz.

## Comandos ejecutados

Desde la raíz, con los archivos privados existentes:

```bash
python3 -m scripts.setup_identity --output .local/development/secrets/identity_keys
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build db proxy backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait db
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --wait proxy image-worker identity-worker
python3 -m scripts.validate_identity --test-path tests/integration/test_identity_delivery.py --test-path tests/test_identity_worker_origin.py --maxfail 3
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

setup_identity es de una sola vez: no repetir ni regenerar claves existentes.
Se reconstruyó backend y se recrearon backend/image-worker/identity-worker tras
la corrección; proxy reiniciado para refrescar resolución del backend.

Validación: 13 pruebas enfocadas aprobadas; build correcto; smoke HTTP aprobado
(HTML español, 10 assets, API mismo origen y protecciones de escritura/cabeceras).
Correo permaneció activo sin reinicio tras la corrección; no se verifica aquí la
entrega de un correo nuevo. Logs privados development-start-* y development-mail-*
en .local/validation. Sin repetir la CI completa, que ya aprobó 3.20 antes del
ajuste; para este ajuste se ejecutaron pruebas relevantes de entrega y origen.

Siguiente: prueba manual con la cuenta existente. Compras todavía no tiene pantalla.
