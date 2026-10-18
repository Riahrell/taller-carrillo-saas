# Operación de identidad y puerta de producción

Implementación y validación conjunta en progreso. Resend está configurado mediante
archivos privados externos; el usuario confirmó recepción de los dos mensajes de
transporte autorizados. No contenían enlaces operativos. SSH `contabo` comprobado
mediante inventario de solo lectura: reloj sincronizado, sin proxy activo ni
certificados operativos encontrados. DNS raíz/panel resuelve desde la VPS; el
resolvedor local no permitió verificarlo todavía. No se ha cambiado el despliegue
remoto. HTTPS público, renovación y recorrido en teléfono siguen pendientes.
Resultados y comandos reales en [seguimiento](access-closure-work.md).

## Claves y configuración

Generar exclusivamente por comando explícito `python3 -m scripts.setup_identity
--output /directorio-privado/identity_keys`. El directorio padre debe existir y
ser privado (0700). No sobrescribe archivos. El JSON contiene keyrings AES-256
separados para semillas/correo y clave HMAC de cuotas estable. No se imprime su
contenido. Montarlo read-only como `IDENTITY_KEYS_FILE`; nunca en imagen/Git/chat.
Guardar copia cifrada externa separada de la DB y probar ambas juntas. Perder la
clave de semillas impide verificar MFA: no hay fallback a contraseña.

Rotación: añadir clave aleatoria nueva conservando antiguas, cambiar active key,
reiniciar API/worker coordinados, comprobar lectura de sobres viejos y escrituras
con nueva clave. Recifrar bajo lock usuario/credencial con nonce nuevo y AAD
original, sin tocar contador/propietario. No retirar antiguas hasta verificar que
DB, pendientes y copias retenidas ya no dependen de ellas. La clave de cuotas no
se rota como efecto de la de cifrado: requiere transición que conserve ventanas.

SMTP por archivo JSON externo: host, port (465 obligatorio), sender, username y
password_file. En contenedor usar `/run/secrets/smtp_password` como password_file.
El remitente y DNS SPF/DKIM/DMARC deben validarse con el proveedor antes de enviar.
Usar `compose.identity.yaml` solo con SMTP_CONFIG_FILE/SMTP_PASSWORD_FILE reales.
Worker usa la misma imagen backend, sin puerto publicado; salida SMTP por su red.
Origen canónico HTTPS de configuración, nunca Host del navegador. No registrar
cuerpos, direcciones o tokens. Cadena y hostname TLS se verifican sin excepciones.

Cada envío corre en un proceso cancelable con deadline total que cubre DNS/TLS/
autenticación/DATA. Secreto por stdin, no argv/env. Deadline o fallo después de
posible DATA es incierto y no se reintenta. Solo NOT_SENT positivo admite hasta
tres intentos, separados por 60 segundos y con Message-ID estable. Aceptado por
SMTP no significa entregado al buzón; comprobar correo real y bandeja/spam.

Mantenimiento cada minuto en worker, lotes de 100: cifrados caducados se retiran;
leases abandonados se vuelven inciertos, sin resend; cuotas antiguas se retienen
30 días y se eliminan bajo lock saltando filas ocupadas. No borrar eventos ni
metadatos de recuperación automáticamente. Copias/WAL/memoria requieren política
propia; esto no garantiza borrado seguro.

## Restauración y pérdida excepcional

No abrir tráfico tras pg_restore. Restaurar en red aislada, comprobar índice y
contenido real, claves/AAD y migraciones. La copia puede revivir contraseña,
sesiones, enlaces y códigos ya consumidos después del snapshot. Antes de abrir:
revocar sesiones/pendientes/enlaces restaurados, avanzar revisión, invalidar todos
los conjuntos offline restaurados y deshabilitar las contraseñas restauradas con
hash aleatorio no publicado. Conservar factores y obligación MFA. Recuperar la
contraseña solo por buzón ya verificado y exigir MFA normal; regenerar códigos
tras autenticación completa. Quien no conserve factor/buzón queda en cuarentena
hasta recuperación excepcional independiente. No emitir sesión por restaurar DB.

Perder teléfono y códigos offline NO se resuelve solo por correo ni por otro
admin de empresa. Operador mantiene expediente privado fuera de la DB restaurada:
identidad previamente registrada por canal independiente, evidencia del titular,
revisión por dos responsables y verificación fuera de correo comprometido.
Sin evidencia independiente suficiente, rechazar recuperación. Registrar motivo,
responsables y revocaciones; no ejecutar SQL de desactivación MFA, compartir
semilla o crear contraseña maestra. La recuperación excepcional no tiene endpoint
ni automatización; requiere procedimiento operativo supervisado y probado antes
de producción. Si el operador no puede proporcionar esa separación, no habilitar
recuperación excepcional ni declarar ese criterio cerrado.

## Aceptación externa pendiente

Proveedor/remitente y recepción real; dominio/origen final (panel independiente),
TLS válido con renovación comprobada, cookies Secure/HttpOnly/SameSite, proxy
privado de DB/API, copias cifradas externas/monitorización y ensayo de recuperación.
No afirmar puerta de inventario superada hasta registrar evidencia y resolver las
brechas acordadas; no basta completar componentes o modificar este documento.

## TLS revisable y comandos de preparación

Preparar un entorno separado (nunca sobre una base activa):
`python3 -m scripts.setup_production --origin https://DOMINIO_DEL_PANEL`.
Renderizar configuración con `python3 -m scripts.render_tls_proxy --hostname
DOMINIO_DEL_PANEL --output RUTA_PRIVADA/nginx.tls.conf`. No hay dominio/IP o
certificado operativo predeterminados. Configurar TLS_PROXY_CONFIG_FILE,
TLS_CERTIFICATE_FILE y TLS_PRIVATE_KEY_FILE fuera del repositorio, con cadena
válida y clave privada legible por el contenedor mediante secret mount. Mantener
padres privados 0700 en host. Compose production expone únicamente 80/443;
la base y API siguen privadas. El proxy interno de salud solo escucha loopback.

Antes de iniciar, inspeccionar puertos/proxy existentes por SSH, respaldar datos,
y revisar `docker compose ... -f compose.production.yaml -f compose.identity.yaml
config`. No sustituir ciegamente un proxy existente. La emisión/renovación ACME
concreta depende de lo que haya en Contabo; no se implementa una instalación
remota suponiendo acceso/configuración que no están confirmados. El hook de
renovación debe recrear el proxy para renovar secret mounts ligados al inode,
validar certificado/cadena y comprobar HTTPS. HSTS solo después de ese ensayo;
no activar preload o includeSubDomains por defecto.

La política CSP del frontend usa nonce por petición y SSR dinámico, sin
unsafe-inline en producción. Referencia técnica consultada:
https://nextjs.org/docs/app/guides/content-security-policy . El QR se genera
localmente con qrcode 8.2, sin transmitir semilla a servicios externos; fuente:
https://pypi.org/project/qrcode/ . No registrar ni almacenar respuesta de setup.

La política local bloquea contraseñas del listado común SecLists con revisión y
SHA-256 fijados en app/data/common-passwords-source.json y licencia incluida.
Se aplica a contraseñas nuevas, sin recortar/modificar la contraseña y sin enviar
su contenido a terceros. No es consulta exhaustiva de todas las contraseñas
filtradas ni una certificación de fortaleza. Archivo íntegro obligatorio al iniciar.

Desactivar seguimiento de aperturas/clics y plantillas con recursos externos para
correo de identidad; verificar en la recepción real que los enlaces conservan el
fragmento y no se reescriben a un rastreador. No incluir contraseñas/semillas/códigos
offline en correo. El token del enlace y su copia manual pertenecen exclusivamente
al propósito del mensaje y caducan; nunca usar permiso de incorporación como reset.
