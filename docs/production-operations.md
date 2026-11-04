# Operación de producción

Guía de mantenimiento de una instalación existente. El último despliegue
comprobado en el proyecto es del 25 de octubre de 2026; esta revisión documental
no comprobó el estado actual de la VPS. Consulta [Estado y versiones](project-status.md)
antes de elegir código, imágenes o migraciones.

## Servicios y configuración

La instalación registrada utiliza releases identificados por commit, una ruta
`current` que selecciona la versión y configuración privada fuera del repositorio.
El proyecto Compose es `carrillo-production`; solo Nginx publica HTTP/HTTPS.
PostgreSQL, backend, frontend y workers no publican puertos propios.

| Pieza                 | Función                                                             |
| --------------------- | ------------------------------------------------------------------- |
| Backend y frontend    | API y pantallas de la versión elegida.                              |
| PostgreSQL            | Base persistente; sus migraciones se aplican durante el despliegue. |
| Proxy                 | TLS, separación de sitio/panel y límites HTTP.                      |
| Worker de imágenes    | Procesa y mantiene fotografías en almacenamiento privado.           |
| Worker de identidad   | Envía mensajes solicitados en la aplicación.                        |
| Servicios programados | Arranque del sistema, copias y renovación de certificados.          |

La configuración privada contiene rutas y valores de DB, identidad, SMTP, S3 y
TLS. No la copies a Git ni a un PR. `.env.example` no es un archivo de producción.
No ejecutes `setup_production`, `configure_production_host` ni herramientas de
retirada antigua sobre la instalación existente como si fuera una actualización.

## Comprobaciones habituales

Estos comandos se ejecutan **dentro de la VPS** y no imprimen los archivos privados:

```bash
/usr/local/sbin/taller-carrillo-compose ps
/usr/local/sbin/taller-carrillo-compose logs --since 15m --tail 100 backend frontend proxy
systemctl status taller-carrillo.service --no-pager
systemctl status taller-carrillo-backup.timer --no-pager
```

Comprueba por separado la página pública, el panel y una operación autorizada.
Un contenedor saludable no demuestra que funcionen correo, imágenes o recorridos
completos. Antes de compartir logs, revisa que no contengan datos privados.

## Actualizar una versión

1. Elegir el commit exacto y revisar su CI, alcance y migraciones. No suponer que
   GitHub, la copia local y la VPS tienen el mismo código.
2. Obtener y verificar una copia recuperable antes de cambios de esquema o datos.
3. Preparar la release y construir imágenes identificables conservando la anterior.
4. Revisar configuración y compatibilidad con el esquema. No regenerar secretos.
5. Aplicar únicamente las migraciones previstas con la credencial de migración.
6. Cambiar los servicios necesarios y comprobar salud, rutas, acceso y recorridos.
7. Registrar commit, imágenes, revisión de Alembic, comprobaciones y reversión.

La validación de CI no despliega. Este repositorio no tiene un único comando de
actualización que se pueda ejecutar sin revisar la instalación existente.
No uses un `down -v` ni restaures una base para corregir un fallo de interfaz.

## Reversión

Conserva la release anterior y una copia de la configuración que se cambiará.
Revertir una imagen no revierte automáticamente las migraciones. Comprueba que
el código anterior puede funcionar con el esquema actual; algunas migraciones
rechazan un downgrade que destruiría evidencia.

Al sustituir archivos montados por Docker, comprueba que el contenedor utiliza
el contenido nuevo. Reemplazar un archivo por otro inode puede requerir recrear
solo el servicio afectado; una recarga de Nginx no actualiza por sí sola un
certificado montado individualmente desde otro inode.

## Copias y restauración

En la instalación registrada existe un timer de copias cifradas y una copia fuera
de la VPS. La clave privada de recuperación se mantiene fuera del servidor.
Una copia útil necesita datos, imágenes, metadatos y el material de identidad que
corresponda, protegidos dentro del conjunto cifrado.

Para solicitar una ejecución del servicio ya configurado:

```bash
systemctl start taller-carrillo-backup.service
systemctl status taller-carrillo-backup.service --no-pager
```

Comprueba resultado y destino, no solo que el comando haya arrancado. Revisa la
retención local y externa y ensaya el descifrado/restauración en una base aislada.
La retención externa no debe darse por configurada sin comprobarla.

Antes de abrir una base restaurada, aplica la cuarentena de accesos y concilia
inventario e imágenes. No arranques workers de correo o limpieza contra una copia
sin revisar. No publiques claves de recuperación, passphrases, archivos SMTP/S3 ni
archivos de administrador como evidencia de un ensayo.

## HTTPS y Cloudflare

La V1 registrada usa dominios separados para sitio y panel, certificados de origen
válidos y Cloudflare en modo **Completo (estricto)**. La corrección de octubre
mantiene las respuestas API con `Cache-Control: no-store, no-transform` para que
las transformaciones de contenido no invaliden las versiones usadas al editar.

- No aplicar una regla de caché general al panel o las API privadas.
- Conservar la CSP con nonce y los controles de origen/CSRF.
- Mantener los registros de correo en Solo DNS cuando corresponda al proveedor.
- Comprobar renovación del certificado de origen aunque exista certificado de borde.
- Antes de restringir el firewall a Cloudflare, revisar sus rangos vigentes,
  la recuperación de IP real desde proxies de confianza y la renovación ACME.

En la instalación con Let's Encrypt, verificar la configuración antes de usar:

```bash
certbot renew --dry-run
```

No habilitar preload o `includeSubDomains` como consecuencia automática de activar
HTTPS. No confundir `Server: cloudflare` con una prueba de autorización del backend.

## Diagnóstico rápido

| Síntoma                               | Qué comprobar                                                                           |
| ------------------------------------- | --------------------------------------------------------------------------------------- |
| Sitio público sin productos           | Empresa configurada y fichas activas/publicadas, en una versión que incluya vitrina.    |
| Correo solicitado sin recibir         | Worker de identidad, transporte, cuotas y recepción del proveedor.                      |
| Fotografías sin cargar                | Worker de imágenes, configuración S3 y autorización de la publicación/producto.         |
| Error de sesión                       | Estado HTTP real: 401, 403, respuesta inválida o fallo de conexión son casos distintos. |
| Lectura 200 pero formulario bloqueado | Contexto de respuesta y ETag; conservar versiones fuertes e `If-Match`.                 |
| Respuesta 403 externa                 | Distinguir una regla/desafío de Cloudflare de un rechazo de la aplicación.              |
| CI falla por seguridad                | Revisar hallazgos y caducidad de excepciones; no desactivar comprobaciones.             |

Los informes antiguos de despliegue y aceptación siguen en el historial de Git y
en las entregas. No sustituyen estas comprobaciones ni acreditan parches actuales.
