# Acceso con seis dígitos por correo

Implementación local: contraseña en cada acceso; correo con seis números cuando
el navegador no tiene una verificación válida de 15 días. Sin QR en el nuevo
inicio de sesión. No es una sesión de 15 días: siguen los plazos de sesión
existentes. El permiso no se renueva al usarlo.

El código conserva ceros iniciales, vence diez minutos después de validar la
contraseña, se consume una vez y se vincula al navegador/intento. Reenvío manual:
mínimo 60 segundos, máximo tres envíos por cuenta por ventana fija de quince
minutos; un nuevo envío reemplaza al anterior sin ampliar el intento.
Cinco errores bloquean el intento y la cuota de cuenta durante la ventana
existente; crear otro intento no elude esa cuota. Hay límite adicional por IP.

Permiso opaco en cookie HttpOnly, SameSite Strict y Secure en producción,
hash almacenado, vinculado a cuenta/dirección/revisión. Contraseña, revisión,
inactividad de cuenta y revocación global se comprueban en servidor.
Un cierre ordinario conserva el permiso; otro navegador no lo hereda.
La sesión y la autorización financiera mantienen sus controles y la contraseña
reciente para operaciones administrativas. No se alteraron precios ni stock.

Código con secrets.randbelow y verificador HMAC con clave externa; payload de
correo cifrado con propósito separado. No hay códigos en respuestas ni logs.
El worker reclama antes de SMTP y no repite automáticamente envíos inciertos.
Si hay múltiples empresas, solo muestra las membresías actuales del usuario y
consume la prueba al emitir sesión. La confirmación por correo también deja
evidencia para el flujo existente de recuperación de contraseña.

## Archivos completos

- backend/app/modules/access/email_login.py y email_login_models.py
- backend/migrations/versions/0037_email_browser_login.py
- backend/app/modules/access/{authentication,email_outbox,http_security,identity_http,identity_routes,mailbox_verification,models,routes,sessions}.py
- backend/app/tools/identity_worker.py
- frontend/src/components/{email-login-flow,access-panel,password-recovery,security-panel}.tsx
- frontend/src/lib/access/{api,identity}.ts
- docs/email-login-policy.md
- este registro

API nueva: POST /auth/email-login/request con {}, y
POST /auth/email-login/confirm con {"code":"<seis números>"}.
Ambas requieren el contexto previo a sesión, origen y CSRF existentes.
El resultado de /auth/login puede ser 202 {"status":"email_code"}.
El navegador usa el prefijo /api del proxy.

## Operación y validación real

Respaldo privado previo:
.local/development/backups/before-email-0037-20261021.dump.
Migración 0037 aplicada sin borrar datos; se conservaron cuentas y contraseñas.
No se afirma restauración del respaldo. Se alinearon dos nombres de restricciones
UNIQUE de las columnas nuevas; el archivo de migración incluye esos nombres.
VPS sin cambios.

Comandos desde la raíz, con los archivos privados ya configurados:
```bash
.venv/bin/ruff check backend/app/modules/access backend/app/tools/identity_worker.py backend/migrations/versions/0037_email_browser_login.py
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
cd ..
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --wait
git diff --check
```

Ruff, TypeScript y ESLint correctos; imágenes compiladas. Configuración del servicio
de correo cargada sin mostrar secretos. Migración confirmada en la base local.
Registro de build/migración/esquema/arranque en .local/validation/email-login-*.
No se ejecutó pytest, Vitest, smoke funcional ni envío de correo desde el agente,
por petición del propietario. No se declara que pasó CI: las expectativas del
contrato anterior de login/TOTP deben adaptarse a esta modalidad antes de dar por
cerrada su regresión automatizada. Los servicios internos antiguos se conservan
para no destruir evidencia, pero el login público ya usa el correo.

## Pruebas manuales del propietario

1. Recargar http://localhost:8080, salir si existe sesión e introducir la
   contraseña habitual. Debe aparecer «Verifica tu inicio de sesión», sin QR.
2. Pulsar «Enviar código por correo». El correo debe contener seis números.
   Verificar que el botón muestre la espera de 60 segundos. Cambiar a correo y
   volver debe conservar el paso. No compartir el código.
3. Introducir el código más reciente, incluyendo ceros iniciales. Debe acceder
   o pedir elegir entre las empresas a las que pertenece la cuenta.
4. Cerrar sesión y volver con contraseña en ese mismo navegador: no debe pedir
   otro correo mientras dure el permiso.
5. Usar otra ventana privada/navegador: contraseña y código deben exigirse.
6. En una nueva sesión privada probar un solo código incorrecto y luego el
   correcto. Si se prueba reenvío, esperar 60 segundos; solo vale el último.
   No agotar cinco errores ni tres envíos si se necesita seguir entrando.
7. Opcional: dejar el intento diez minutos sin completar; debe rechazarlo.
   Cancelar y empezar de nuevo, respetando cuotas.
8. Desde inventario/catálogo confirmar identidad con contraseña; no debe pedir
   código de aplicación autenticadora.
9. Recuperar/cambiar contraseña desde la propia cuenta invalida permisos
   anteriores. Este paso es opcional y lo realiza exclusivamente el propietario.

La caducidad exacta de 15 días y la concurrencia quedan pendientes de validación
funcional; no se han acortado tiempos ni añadido accesos de prueba.
Siguiente tarea: recoger resultados manuales y adaptar los contratos de regresión
del flujo de acceso antes de considerar cerrada esta fase de seguridad.
