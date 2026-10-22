# 3.55 — Verificación y mantenimiento de parches de VPS

Continuación del pedido del propietario de administrar la VPS y comprobar los
parches comentados previamente. La VPS ya estaba sin aplicaciones por la retirada
3.54. No se actualiza el equipo personal ni se accede al Object Storage.

## Revisión previa

Ubuntu 24.04.5 LTS, kernel activo 6.8.0-147-generic; linux-virtual y
linux-image-virtual 6.8.0-147.147. Docker 29.9.1, Compose 5.6.0,
containerd.io 2.4.2-2 y Buildx 0.38.0. Docker y unattended-upgrades habilitados.
No había reinicio pendiente.

Se refrescó el índice con apt-get update y APT::Update::Error-Mode=any; resultado
correcto. Simulación --no-remove --with-new-pkgs upgrade: 11 actualizaciones,
0 paquetes nuevos, 0 retiradas, 5 aplazadas. Se comprobó que la VPS seguía sin
contenedores y que sshd -t era correcto antes de proceder. No se fuerza el
phasing de Ubuntu ni se cambian repositorios, distribución o rama del kernel.

## Fuentes oficiales consultadas el 22 de octubre de 2026

- https://ubuntu.com/security/CVE-2026-99999: Canonical sigue marcando linux en
  Ubuntu 24.04 Noble como vulnerable; no se declara corregido este CVE por
  actualizar otros paquetes. En los repositorios configurados no se encontró
  un kernel candidato posterior al instalado.
- https://docs.docker.com/engine/release-notes/29/: Docker 29.9.2 actualiza Go para
  rechazar cadenas que usan intermediarios eMudhra revocados (CVE-2026-111943).

La corrección del host físico de Contabo es independiente y no se demuestra desde
el kernel invitado ni el aviso reenviado. No se pudo consultar el artículo público
de Contabo mediante la herramienta web; no se infiere por ello su estado.

## Actualización revisada

Se ejecutó la actualización normal de los 11 paquetes disponibles, con --no-remove,
DEBIAN_FRONTEND=noninteractive y Dpkg::Options::=--force-confold para conservar
configuraciones existentes. El procedimiento compara de nuevo el número de cambios
antes de instalar y se detiene si cambia el plan. No se ordena reiniciar la VPS.

## Resultado ejecutado y verificado

Actualización con exit code 0: 11 paquetes actualizados, 0 nuevos, 0 retirados.
Se actualizaron Docker CE/CLI/rootless extras, containerd.io, Buildx, Compose,
Docker Model plugin, ubuntu-pro-client/l10n, snapd y tmux.

La comprobación desde una conexión SSH nueva confirma:

| Componente | Estado final |
| --- | --- |
| Docker CE y CLI | 29.9.2 |
| Compose | 5.6.1 |
| containerd.io | 2.4.2-3~ubuntu.24.04~noble |
| Buildx | 0.38.1 |
| Kernel activo | 6.8.0-147-generic |
| linux-virtual instalado/candidato | 6.8.0-147.147 / 6.8.0-147.147 |
| dpkg --audit | sin problemas reportados |
| Reinicio requerido | no; no se reinició el servidor |
| Docker | activo y habilitado al arrancar |
| SSH | conexión nueva y sshd -t correctos |
| Contenedores, imágenes, volúmenes y caché | cero |
| Puertos TCP en escucha | 22 y 53 |

La simulación posterior no propone más instalaciones: 0 actualizaciones aplicables
ahora y cinco aplazadas por phasing, cloud-init y cuatro paquetes de GLib. APT confirma aplazamiento por phasing al 40% y apt-mark showhold no devuelve
bloqueos manuales. No se forzó su instalación. No se encontró canonical-livepatch ni entradas de livepatch
en el kernel; la corrección de CVE-2026-99999 no queda demostrada. El estado del
host físico requiere información del proveedor, independiente de esta revisión.

También se detectó systemd-networkd-wait-online.service marcado como fallido.
No se limpió el indicador para aparentar un resultado correcto: se conserva como
pendiente de diagnóstico del arranque de red antes de programar un reinicio.
La conectividad SSH está operativa; systemd-networkd y systemd-resolved están
activos. eth0 figura routable/configuring. No se modifica la configuración de red.

Comandos ejecutados por SSH, con salidas revisadas sin datos secretos:

```sh
apt-get update -o APT::Update::Error-Mode=any
apt-get --simulate --with-new-pkgs --no-remove upgrade
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -y --no-remove --with-new-pkgs -o Dpkg::Options::=--force-confold upgrade
/usr/sbin/sshd -t
dpkg --audit
docker version --format '{{.Server.Version}}'
docker compose version --short
apt-cache policy linux-virtual linux-image-virtual
systemctl --failed --no-legend --no-pager
```

Recibo local ignorado: .local/validation/vps-patches-3.55.json. Archivos cambiados:
este documento, docs/staging-plan.md y docs/security-stack-review-2026-10-21.md.
No cambia código de la app ni se repiten sus suites por una actualización del host.
La corrección del fallo de estilo de GitHub se documenta separadamente en 3.56.

Sigue cerrar el estado del kernel y diagnosticar la espera de red, y preparar el
despliegue HTTPS. No se declara el servidor libre de vulnerabilidades ni se ha
publicado aún la aplicación nueva.
