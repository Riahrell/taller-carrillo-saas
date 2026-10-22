# Pruebas remotas: Contabo y dominio informado

## Estado vigente — 22 de octubre de 2026

El propietario pidió expresamente eliminar todas las aplicaciones y datos de
prueba de la VPS y confirmó que no hay nada que respaldar. Esta instrucción
sustituye la postergación y la condición de conservar/restaurar el despliegue
anterior que aparecen en el histórico de este documento.

Entrega 3.54 ejecutada: retirados los tres contenedores antiguos, dos volúmenes,
dos redes Compose, seis imágenes, caché de compilación y cuatro directorios de
aplicaciones/copias/herramientas. No se creó un respaldo nuevo. La verificación
remota confirma cero contenedores, volúmenes, imágenes y caché; 92 GiB libres.
Sistema operativo, SSH y Docker conservados. Solo puertos TCP 22 y 53 escuchando;
80/443 disponibles. No se accedió al Object Storage ni se alteró el proyecto local.

Mantenimiento 3.55: Docker actualizado a 29.9.2 y Compose a 5.6.1, dpkg sin
problemas y sin reinicio requerido. Kernel 6.8.0-147: Canonical todavía marca
CVE-2026-99999 como vulnerable en Noble y no hay un candidato posterior en APT.
Cinco actualizaciones quedan aplazadas por phasing. Se conserva el pendiente de
diagnosticar systemd-networkd-wait-online.service fallido antes de reiniciar.

Próximo paso: preparar y revisar la instalación HTTPS nueva en
panel.taller-carrillo.com. No hay aplicación nueva ni certificado publicado.
Las copias futuras de la aplicación nueva siguen requiriendo política de respaldo
y recuperación; descartar estos datos de prueba antiguos no elimina ese requisito.
Ver docs/deliveries/legacy-vps-retirement-3.54.md.

## Histórico — prioridad del 18 de octubre de 2026

El usuario solicita dejar la VPS para después y continuar el desarrollo local
del catálogo e inventario. Se detienen las tareas remotas de limpieza, despliegue
y certificados. No eliminar proyectos ni volúmenes por la autorización anterior
mientras esté vigente esta postergación.

Se obtuvo una copia fría del despliegue anterior y se conservó cifrada fuera del
servidor, con integridad de archivos verificada. Su restauración de base todavía
no está validada: el ensayo se detuvo en la comparación de configuración de las
imágenes. No presentarla como respaldo con recuperación demostrada. Los tres
contenedores anteriores fueron reiniciados después de la captura; no se retiró
ningún proyecto ni volumen. No se desplegó la aplicación nueva ni se emitió TLS.

Antes de retomar producción: validar esa restauración, revisar el estado real
del servidor, preparar release y comprobar HTTPS/renovación y recorridos externos.
Estas tareas no bloquean el trabajo local autorizado de módulos comerciales.

Contexto confirmado por el usuario el 2026-10-16: dispone de un VPS en Contabo
que también aloja imágenes; inicialmente preveía comprar en Namecheap. Después
informa que adquirió **taller-carrillo.com** y conectó el DNS a la IP del VPS.
El 2026-10-18 se inspeccionó el VPS por SSH con el alias autorizado `contabo`.
Desde el VPS, raíz y `panel.taller-carrillo.com` resuelven al servidor SSH.
El usuario confirmó la creación del CNAME del panel. No se ha verificado
propagación global ni publicado la nueva aplicación.
No enviar credenciales del VPS/DB/DNS por chat ni incorporarlas al repositorio.

Actualización posterior del usuario: puede retirar el proyecto existente y
autoriza dejar limpia la VPS si es necesario para Carrillo. Esta instrucción
sustituye la obligación inicial de conservar ese despliegue. No hubo limpieza
desde aquí: primero identificar recursos y datos, preparar respaldo recuperable
y concretar qué se retirará. No confundir disponibilidad para limpiar con un
servidor ya inspeccionado o sin datos que respaldar.

## Preparación antes de cambiar el VPS

1. Inventario de solo lectura de proxy/webserver, contenedores, puertos ocupados,
   rutas/volúmenes de imágenes y bases, recursos disponibles y backups. No basta
   con saber el proveedor para suponer una configuración o capacidad.
2. Identificar el proxy que ya usa 80/443 y diseñar el virtual host del subdominio.
   Evitar instalar otro proxy sobre esos puertos sin revisar el servicio existente.
3. Proyecto y secretos independientes para Carrillo staging; PostgreSQL propio
   en red privada, sin reutilizar la base/volúmenes de imágenes o negocio. Usar
   datos de prueba, cuentas individuales y acceso remoto restringido mientras
   las brechas de `security-review.md` sigan abiertas.
4. Preparar archivos y comandos revisables antes de cambiar DNS o ejecutar el
   despliegue. La limpieza está autorizada si hace falta; identificar antes los
   contenedores, imágenes y volúmenes a retirar y respaldar lo recuperable.
   No ejecutar prune global sobre recursos que todavía no se han identificado.

## Dominio y HTTPS

Comprar el nombre no habilita TLS automáticamente. Con el dominio informado,
verificar DNS y elegir un subdominio de pruebas; configurar su DNS y emitir
un certificado válido con renovación automatizada. La integración concreta
depende del proxy existente; no se ha decidido sustituirlo.

Validar origen canónico HTTPS, redirección HTTP->HTTPS, certificado/cadena,
renovación, cookies Secure/HttpOnly/SameSite, CSRF/origen, ausencia de contenido
mixto y cierre de puertos internos. HSTS solo después de comprobar HTTPS y el
alcance de subdominios. No aceptar avisos de certificado para eludir estas pruebas.

Se requieren acceso administrado al servidor y configuración DNS, mediante canal
seguro y permisos mínimos. Son pendientes operativos; todavía no existe evidencia
de despliegue TLS. El HTTP de localhost actual es exclusivamente desarrollo.

## Aceptación del entorno remoto

- Si se conserva el servicio anterior, sigue funcionando; si se retira según la
  autorización actual, se documentan sus recursos y el respaldo de sus datos.
- DB/secretos/redes/volúmenes de staging son independientes y los tests no apuntan
  a recursos operativos. No hay carga de datos reales en staging por defecto.
- Backup privado y restauración aislada ensayados antes de cambios irreversibles.
- Acceso remoto restringido hasta cerrar brechas prioritarias; no basta un dominio
  o certificado para afirmar que la aplicación es segura o apta para producción.
- Smoke HTTPS, permisos entre empresas, rutas sensibles, logs sin secretos y
  recuperación del despliegue comprobados con resultados reales documentados.

Inventario de solo lectura confirmado el 2026-10-18:

- Servicios anteriores: `saas_frontend` (3000), `saas_backend` (8000) y
  `saas_postgres` (5432), publicados en las interfaces del servidor.
- PostgreSQL utiliza `taller-carrillo_postgres_data`; frontend y backend no
  tienen mounts y su filesystem debe conservarse al respaldar.
- `/opt/taller-carrillo/uploads` contiene imágenes; no retirar esa ruta.
- Puertos 80/443 libres, nginx/caddy inactivos, certbot no encontrado.
- NTP sincronizado; aproximadamente 76 GB disponibles en el disco raíz.

No se detuvieron servicios ni se modificaron datos remotos. Siguiente paso:
terminar CI y navegador, preparar respaldo recuperable del sistema anterior y
despliegue aislado para `panel.taller-carrillo.com`, con certificado y renovación.
El dominio público es contexto documental,
no un origen codificado en la aplicación: configurar el origen final por entorno.
No hay contraseñas, IPs públicas o
credenciales predeterminadas codificadas en este documento.

## Autorización de retirada actualizada

El usuario confirmó de nuevo el 18 de octubre que pueden eliminarse los proyectos
anteriores y sus archivos, conservando este proyecto. La limpieza se ejecutará
al efectuar el despliegue, después de inventariar recursos, guardar un respaldo
recuperable y comprobar la nueva instalación. Conservar el sistema operativo,
SSH y los servicios de infraestructura necesarios; no equivale a borrar el
filesystem del servidor ni a ejecutar prune global indiscriminado. Todavía no
se han retirado contenedores, volúmenes o archivos remotos.
