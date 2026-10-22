# Respaldo de imágenes privadas

La herramienta administrativa `app.tools.image_archive` exporta únicamente las
imágenes vigentes de los productos de la base configurada y sus miniaturas. No
lista el bucket completo, no descarga reservas fallidas ni objetos históricos ya
sustituidos. No crea endpoints ni permisos de usuario nuevos. No modifica la base.

## Consistencia con PostgreSQL

Antes de un respaldo operativo, bloquear escrituras y detener backend,
image-worker y cualquier otro productor de imágenes. Esperar a que terminen las
peticiones pendientes. Mantener esa ventana durante pg_dump y la exportación de
objetos, identificando ambos respaldos como un mismo conjunto. Un snapshot SQL
REPEATABLE READ por sí solo no congela los objetos de S3. La opción
`--maintenance-confirmed` registra la confirmación del operador; la herramienta
no impone el mantenimiento ni coordina pg_dump automáticamente.

Exportar en un directorio nuevo de un volumen privado con espacio suficiente.
Si falta un objeto o falla su integridad, la exportación falla sin manifest final;
no presentar los archivos parciales como un respaldo completo ni reutilizar su
carpeta. Conservarlos para diagnóstico privado o retirarlos de forma controlada.

## Comandos

Desde la raíz, con `.venv` preparada y la configuración de base suministrada por
el entorno del operador (`DATABASE_URL_FILE`, sin valores secretos en comandos),
las tres operaciones son:

```sh
PYTHONPATH=backend .venv/bin/python -m app.tools.image_archive export \
  --directory .local/backups/release-images \
  --storage-config /home/riahrell/.config/taller-carrillo/contabo-s3.env \
  --maintenance-confirmed

PYTHONPATH=backend .venv/bin/python -m app.tools.image_archive verify \
  --directory .local/backups/release-images

PYTHONPATH=backend .venv/bin/python -m app.tools.image_archive restore \
  --directory .local/backups/release-images \
  --storage-config /home/riahrell/.config/taller-carrillo/contabo-s3.env \
  --maintenance-confirmed --apply
```

Son ejemplos operativos preparados, NO ejecutados contra Contabo. La carpeta
`.local/backups` debe existir; `release-images` no debe existir antes de exportar.
Las rutas de configuración son privadas y no se leen mediante shell/source.
El archivo S3 admite el formato estricto de image_configuration; no contiene
credenciales en Git. En una recuperación aislada usar configuración explícita
para el bucket privado de destino, antes de cambiar la configuración de la app.

Verify es offline: no requiere base, credenciales ni acceso a S3. Restore requiere
`--apply` y mantenimiento confirmado, valida todos los archivos antes del primer
PUT y publica con If-None-Match *. Un objeto existente idéntico permite repetir;
un objeto distinto causa error sin sobrescribirlo. Cada PUT se confirma mediante
lectura íntegra. Si falla a mitad, conservar el archivo y repetir: no hay borrado
ni rollback destructivo de los objetos ya recuperados. No arrancar la aplicación
hasta completar la restauración de DB y objetos y comprobar sus referencias.

## Correspondencia con la base recuperada

Antes de reabrir la aplicación, con el entorno de base apuntando a la copia
restaurada aislada, ejecutar:

```sh
PYTHONPATH=backend .venv/bin/python -m app.tools.image_archive check-database \
  --directory .local/backups/release-images
```

Este comando verifica primero los archivos y luego compara exactamente todas las
referencias vigentes: claves, hashes, tamaños, formato y dimensiones, incluyendo
miniaturas. No requiere credenciales S3 ni escribe en la base. Falla si faltan o
sobran referencias o si difieren sus metadatos. Que coincidan las imágenes no
prueba la conciliación de existencias: esa comprobación sigue en el ensayo de
restauración del inventario. Mantener la ventana de mantenimiento hasta finalizar
las verificaciones y evitar tomar una coincidencia puntual como autorización para
abrir servicios mientras otros productores siguen escribiendo.

Estado operativo del 22 de octubre de 2026: el propietario confirmó que todavía
no dispone de un bucket de recuperación separado. No se ha creado uno ni se ha
restaurado nada en Contabo. Nombre propuesto, sujeto a disponibilidad:
`taller-carrillo-restauracion-pruebas`, privado. La configuración de destino debe
guardarse fuera de Git; verificar el destino antes de usar --apply y no reutilizar
por error la configuración que apunta a taller-carrillo-imagenes.

## Protección y alcance

Carpeta 0700, archivos 0600, sin enlaces simbólicos, propietarios del operador.
El manifest solo contiene claves internas, hashes, tamaños y dimensiones; no
incluye URLs firmadas, credenciales ni endpoints. Los archivos son privados pero
NO están cifrados: cifrar el conjunto antes de enviarlo fuera de la máquina y
conservar la clave por separado mediante el procedimiento del operador. SHA-256
detecta corrupción respecto del manifest; no autentica un manifest sustituido
por un atacante con acceso a la copia. Conservarlo en un destino confiable.

Una exportación real, una recuperación en un bucket privado independiente y la
coordinación con el dump de producción todavía requieren ensayo operativo. Las
pruebas automatizadas usan PostgreSQL temporal, imágenes reales sintéticas y un
transporte simulado; no demuestran disponibilidad, permisos o cuotas de Contabo.
