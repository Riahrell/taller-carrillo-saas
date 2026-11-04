# Desarrollo local

Los comandos se ejecutan desde la raíz del repositorio. Instalan un entorno de
desarrollo independiente de la VPS; no uses credenciales de producción.

## Requisitos

- Docker Engine y Docker Compose con soporte para `!reset`.
- Python 3.13 para las herramientas del repositorio.
- Git y acceso al daemon Docker.
- Acceso a los registros de imágenes y paquetes durante la primera construcción.
- Node.js 24 únicamente si ejecutarás herramientas frontend fuera de Docker.

Los Dockerfiles y archivos lock fijan las versiones usadas. `.env.example` es una
referencia; el flujo Compose utiliza el archivo generado que se indica abajo.

## 1. Obtener el código

```bash
git clone git@github.com:Riahrell/taller-carrillo-saas.git
cd taller-carrillo-saas
```

Comprueba [Estado y versiones](project-status.md). La rama `main` revisada no
contenía todavía todas las funciones de la V1 operativa.

## 2. Generar el entorno una sola vez

```bash
python3 -m scripts.setup_local development
```

Crea `.local/development/compose.env` y los secretos necesarios para base de datos,
CSRF e identidad. Genera valores aleatorios, no los imprime y rechaza sobrescribir
un entorno existente. `.local/` está excluido de Git.

Si el entorno ya existe, continúa con él. No lo borres para repetir la instalación:
regenerar secretos no cambia las contraseñas de una base ya inicializada.

## 3. Definir el comando de desarrollo

Esta función evita repetir las opciones. Vuelve a definirla si abres otra terminal.

```bash
dc() {
  docker compose \
    --project-name carrillo-dev \
    --env-file .local/development/compose.env \
    -f compose.yaml \
    -f compose.dev.yaml \
    "$@"
}
```

## 4. Construir y arrancar la instalación mínima

```bash
dc build db backend frontend proxy
dc up -d --wait db
dc run --rm migrate
dc up -d --wait proxy
dc ps
```

Abre [http://localhost:8080](http://localhost:8080). La API de salud está en
`http://localhost:8080/api/health/live`. Usa `localhost`, porque el origen de
desarrollo generado es exactamente `http://localhost:8080`.

Solo el proxy publica un puerto local. PostgreSQL, FastAPI y Next.js se comunican
por las redes de Compose. La migración se ejecuta explícitamente; arrancar el
backend no actualiza el esquema.

## 5. Correo e imágenes

La instalación mínima permite abrir la interfaz, pero no configura un proveedor
SMTP ni almacenamiento S3. Para completar recorridos de correo y fotografías
hacen falta esos servicios. No existe un modo de omitir la verificación de acceso.

| Función                          | Overlay                 | Variables de ruta en el entorno privado   |
| -------------------------------- | ----------------------- | ----------------------------------------- |
| Correo solicitado por el usuario | `compose.identity.yaml` | `SMTP_CONFIG_FILE`, `SMTP_PASSWORD_FILE`. |
| Fotografías y su worker          | `compose.images.yaml`   | `S3_CONFIG_FILE`.                         |

Prepara archivos privados externos siguiendo los formatos de
[operación de identidad](identity-operations.md#claves-y-configuración) y del
[contrato de imágenes](product-images-contract.md). No pegues claves en comandos,
capturas, commits ni solicitudes de soporte.

Cuando ambos proveedores estén configurados, sustituye la función por esta
variante; los pasos de construcción/migración/arranque siguen siendo los mismos:

```bash
dc() {
  docker compose \
    --project-name carrillo-dev \
    --env-file .local/development/compose.env \
    -f compose.yaml \
    -f compose.dev.yaml \
    -f compose.images.yaml \
    -f compose.identity.yaml \
    "$@"
}

dc build backend frontend
dc up -d --wait proxy image-worker identity-worker
```

## 6. Crear el primer administrador

Con la base migrada, ejecuta en una terminal interactiva:

```bash
dc run --rm --interactive --no-tty=false backend \
  python -m app.modules.access.bootstrap
```

Solicita empresa, correo y contraseña con entrada oculta. Solo funciona sobre una
base sin registros de acceso: no es un restablecimiento de contraseñas ni una
cuenta de demostración. Completa después el recorrido de correo o MFA que indique
la aplicación. Sin transporte de correo operativo no se puede validar ese flujo.

## Consultar la revisión de la base

```bash
dc run --rm migrate python -m alembic -c alembic.ini current
```

Para un entorno con datos, realiza una copia antes de aplicar migraciones y revisa
las notas de la versión. No ejecutes un downgrade suponiendo que es reversible.

## Trabajo habitual

```bash
dc ps
dc logs --since 10m --tail 100 backend frontend proxy
dc build backend frontend
dc up -d --wait proxy
```

Tras modificar `ops/proxy/nginx.conf`, recrea el proxy para cargar el archivo del
repositorio sin depender de un montaje anterior:

```bash
dc up -d --no-deps --force-recreate proxy
```

Para detener conservando los datos:

```bash
dc down
```

No añadas `--volumes` o `-v` si quieres conservar la base.
Las [pruebas](testing.md) utilizan un proyecto y una base temporales separados.

## Problemas habituales

| Síntoma                       | Revisar                                                                             |
| ----------------------------- | ----------------------------------------------------------------------------------- |
| El entorno ya existe          | Reutilizar sus archivos; no regenerar credenciales.                                 |
| Error de esquema              | Imagen backend seleccionada y revisión de Alembic.                                  |
| Login sin correo              | Proveedor, configuración privada y `identity-worker`.                               |
| Fotos no disponibles          | `compose.images.yaml`, configuración S3 y `image-worker`.                           |
| Origen o CSRF rechazado       | Acceder por `localhost:8080` y conservar `PUBLIC_ORIGIN` exacto.                    |
| El código cambiado no aparece | Reconstruir la imagen correspondiente; los servicios no son un servidor hot-reload. |
