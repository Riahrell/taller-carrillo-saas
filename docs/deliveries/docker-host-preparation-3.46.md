# 3.46 — Preparación del mantenimiento local de Docker

Estado: preparación ejecutada; instalación pendiente de contraseña administrativa
en la terminal del propietario. No se actualizó el motor ni se tocó Contabo.

## Comprobaciones reales

- Debian 13 trixie; instalación oficial Docker CE mediante APT.
- Docker cliente/servidor 29.9.1; Compose 5.6.0.
- Repositorio configurado ofrece Docker 29.9.2 y Compose 5.6.1.
- Simulación: cuatro paquetes actualizados (motor, CLI, rootless extras y Compose),
  ninguno instalado de cero ni eliminado. No se ejecuta autoremove.
- Seis contenedores de Carrillo activos y un contenedor adicional `saas_postgres`.
- Live restore desactivado: el mantenimiento puede interrumpir todos los
  contenedores. No se administra ni respalda la base ajena desde este proyecto.
- `sudo -n true` exige contraseña. No se solicita ni almacena en el chat.
- Smoke HTTP previo aprobado.

Respaldo local de Carrillo: `.local/backups/pre-docker-2026-10-21/carrillo.dump`,
formato custom, 234052 bytes, directorio 0700, archivo 0600 y SHA-256 en archivo
privado. `pg_restore --list` comprobó 438 entradas del catálogo. No se realizó
restauración de ese respaldo; verificar el catálogo no demuestra restaurabilidad.
El volumen original no se modificó. El respaldo está ignorado por Git.

## Comando preparado para ejecutar en la terminal local

La contraseña se introduce exclusivamente en el prompt local de sudo. Se fijan
las versiones examinadas en la simulación, sin actualizar otros paquetes del SO:

```bash
sudo apt-get install --only-upgrade \
  docker-ce=5:29.9.2-1~debian.13~trixie \
  docker-ce-cli=5:29.9.2-1~debian.13~trixie \
  docker-ce-rootless-extras=5:29.9.2-1~debian.13~trixie \
  docker-compose-plugin=5.6.1-1~debian.13~trixie
```

## Comprobación posterior pendiente

```bash
docker version --format 'Client={{.Client.Version}} Server={{.Server.Version}}'
docker compose version --short
docker ps --format '{{.Names}}\t{{.Status}}'
```

Si la instalación terminó correctamente, recuperar los servicios de Carrillo
desde la raíz del repositorio, conservando sus volúmenes:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait db
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend proxy identity-worker image-worker
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

No borrar volúmenes ni restaurar encima de la base como parte de una actualización
normal. El propietario debe comprobar también su contenedor adicional si fue
interrumpido; no se modifica desde el Compose de Carrillo.

## Archivos, riesgos y siguiente tarea

Solo cambia esta documentación; sin cambios de aplicación ni nuevas pruebas de
negocio. No se declara actualizado Docker/Compose hasta comprobar cliente y
servidor después de instalar. Siguen los cuatro avisos npm de desarrollo y los
pendientes funcionales/operativos de 3.45. Próximo paso: completar la instalación
administrativa y validar la recuperación local; después aceptación manual de
inventario y cierre del alcance para VPS.
