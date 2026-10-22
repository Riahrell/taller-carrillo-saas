# 3.47 — Recuperación de Docker y revisión CVE-2026-99999

Fecha local de revisión: 21 de octubre de 2026, America/El_Salvador.

## Resultado local comprobado

Después de la instalación administrativa realizada por el propietario:

- Docker cliente y servidor: 29.9.2; Compose: 5.6.1.
- Se recuperaron db, backend, frontend, proxy y los dos workers con el Compose
  existente. Los cuatro servicios con healthcheck quedaron saludables; workers
  activos. La web de localhost:8080 aprobó el smoke HTTP.
- Se conservaron los volúmenes. No se restauró encima de la base ni se tocó el
  contenedor adicional del propietario.
- Debian 13: kernel en ejecución `6.12.113+deb13-amd64`, paquete `6.12.113-1`.
  Coincide con la versión corregida para CVE-2026-99999 en el tracker de Debian.
  El metapaquete linux-image-amd64 también está instalado en esa versión.
- Persisten actualizaciones locales disponibles, entre ellas ca-certificates,
  Firefox ESR, containerd y Buildx. No se instalaron desde esta entrega porque
  requieren contraseña administrativa local.

## Comprobación de la VPS exclusivamente de lectura

Se usó el alias autorizado `contabo`, sin imprimir direcciones, credenciales ni
datos de aplicación. Ubuntu 24.04.5 LTS, kernel `6.8.0-147-generic`.
`linux-virtual` y `linux-image-virtual` instalados en `6.8.0-147.147`, manteniendo
el seguimiento de futuros kernels. El índice APT observado está actualizado al
21 de octubre en horario local. No ofrecía otro kernel para esa rama.

Canonical todavía marca linux de Ubuntu 24.04 noble como vulnerable al CVE en
la ficha consultada. No se deduce corrección por tener la versión candidata ni
por no existir marcador de reinicio. Tampoco se confirmó el estado del host
físico de Contabo desde dentro de la máquina virtual.

Docker remoto está activo y habilitado para iniciar con el sistema. Los tres
contenedores existentes se observan activos; no se comprobaron sus datos ni
integridad transaccional, ni se leyeron logs de negocio. Hay actualizaciones
remotas pendientes de Docker/Compose, containerd, Buildx y otros paquetes.
No se instaló, reinició ni reconfiguró nada en la VPS.

## Interpretación del aviso de Contabo

El correo proporcionado por el propietario describe mantenimiento del kernel
del host de virtualización administrado por Contabo. Es una responsabilidad
distinta de mantener actualizado el sistema operativo invitado de la VPS.
Reiniciar el invitado no demuestra que el host esté parcheado. La página pública
de estado no permite atribuir un estado de parche a este servidor concreto.
El artículo de soporte enlazado no pudo recuperarse con el navegador de consulta;
no se presenta su contenido como verificado independientemente del correo.

## Comandos efectivamente ejecutados

```bash
docker version --format 'Client={{.Client.Version}} Server={{.Server.Version}}'
docker compose version --short
uname -r
apt list --upgradable
dpkg-query -W linux-image-amd64 linux-image-6.12.113+deb13-amd64
apt-cache policy linux-image-amd64
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait db
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend proxy identity-worker image-worker
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Las consultas SSH fueron solo uname, os-release, paquetes instalados/candidatos,
actualizaciones disponibles, marcador de reinicio y estado/arranque de Docker.
No se realizaron pruebas de explotación. Sin cambios de código: no se repiten
las suites de negocio por una inspección y recuperación de servicios.

## Recomendación pendiente

Mantener actualizaciones oficiales de ambos sistemas, sin cambiar de distribución
ni instalar kernels mainline improvisados. El PC ya tiene el parche de este CVE;
debe completar las otras actualizaciones de seguridad, especialmente certificados
y navegador. Para la VPS, preparar respaldo y ventana de mantenimiento para los
paquetes disponibles, seguir la corrección de Canonical y comprobar el estado
del host asignado con Contabo. El reinicio remoto se programa solo cuando proceda.

Esta recomendación no es una instalación ejecutada. Sigue pendiente la aceptación
manual del inventario y el cierre de despliegue de Carrillo.

## Fuentes primarias

- [Debian, CVE-2026-99999 y versión corregida](https://security-tracker.debian.org/tracker/CVE-2026-99999).
- [Canonical, estado por rama de kernel](https://ubuntu.com/security/CVE-2026-99999).
- [Debian DSA-6572-1: certificados](https://lists.debian.org/debian-security-announce/2026/msg00487.html).
- [Estado público de Contabo](https://contabo-status.com/).
