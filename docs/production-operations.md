# Operación del despliegue inicial

El propietario autorizó el despliegue de inicio de sesión e inventario, con una
base nueva y un administrador de Taller Carrillo. Decidió aplazar el parche del
kernel que Canonical todavía no ofrece para la rama instalada. No se considera
corregido ese CVE ni se suprimen las excepciones de seguridad vigentes.

La instalación usa releases identificados por commit bajo /opt/taller-carrillo,
configuración/secretos externos bajo /etc/taller-carrillo y el proyecto Compose
carrillo-production. Solo el proxy publica 80/443. PostgreSQL, backend, frontend
y workers no publican puertos. El firewall del host permite 22/80/443.

Comandos de administración, en la VPS:

```sh
/usr/local/sbin/taller-carrillo-compose ps
systemctl status taller-carrillo.service --no-pager
systemctl status taller-carrillo-backup.timer --no-pager
systemctl start taller-carrillo-backup.service
```

No ejecutar setup/configure_production_host de nuevo: la inicialización rechaza
entornos existentes para evitar reemplazar claves o datos. Una actualización
requiere nueva release, validación, respaldo, migración revisada y selección de
imágenes; conservar al menos la release anterior para rollback compatible con
el esquema. No usar el script de retirada antigua después de instalar el sistema.

HTTPS usa Let's Encrypt. La primera emisión usa standalone; después se configura
renovación webroot mediante /.well-known/acme-challenge/. El hook valida cadena,
hostname, vigencia y pareja de claves, publica ambos archivos en una versión
privada y recrea únicamente el proxy para que Docker cargue los nuevos inodos.
No basta con reload de Nginx para secretos montados individualmente. La prueba
certbot renew --dry-run debe terminar correctamente antes de dar la renovación
por comprobada. No aceptar certificados inválidos en pruebas externas.

El servicio systemd de aplicación arranca Compose con --wait después de Docker y
network-online. Los contenedores tienen restart unless-stopped. Las migraciones
se aplican durante despliegue, no en cada arranque. No se reinicia el host para
probarlo sin una ventana explícita de mantenimiento.

Los respaldos se programan a las 03:00 de America/El_Salvador, con hasta cinco
minutos de dispersión. Se detienen escritores brevemente para coordinar pg_dump,
objetos y metadatos; se reanudan incluso ante fallo, antes de cifrar y transferir.
El conjunto incluye las claves de identidad necesarias para descifrar el estado
restaurado, dentro del archivo cifrado, sin imprimirlas. Se conservan siete copias
encriptadas locales y copias verificadas en taller-carrillo-restauracion, prefijo
respaldos/. La herramienta no borra objetos externos. Configurar retención externa
cuando se acuerde; controlar su crecimiento. Límite actual por archivo: 4 GiB;
una copia mayor falla explícitamente y requiere ampliar la estrategia de subida.

La VPS contiene solo la clave pública de recuperación. La clave privada y su
passphrase están en la computadora del propietario, en .local/deployment,
protegidas y fuera de Git; se necesita conservarlas para recuperar las copias.
No enviar esos archivos por chat ni copiarlos a la VPS. Mantener una segunda
copia privada protegida de ese material en un lugar independiente del equipo.

Antes de activar una base restaurada se requiere cuarentena de accesos, comprobar
el estado del esquema y conciliar inventario e imágenes. La copia cifrada debe
verificarse y ensayarse en infraestructura aislada, sin arrancar workers de correo
o limpieza sobre datos restaurados. Una copia nueva no implica restauración
comprobada hasta ejecutar ese ensayo.

La cuenta inicial usa contraseña aleatoria única guardada en un archivo privado
local. No se incorporan credenciales por defecto ni se envían contraseñas por
correo. El usuario solicita sus códigos y recuperación desde la aplicación.
Los resultados reales del despliegue se registran en la entrega correspondiente;
este procedimiento no afirma que un servicio ya esté publicado.
