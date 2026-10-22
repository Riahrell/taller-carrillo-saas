# 3.50 — Recuperación de referencias de fotos y miniaturas

El ensayo de restauración ahora incluye un producto con una reserva WebP adjunta,
su enlace vigente y su evento de publicación. Comprueba claves, empresa/producto,
responsable, formato, hashes, tamaños y dimensiones de ambas variantes.

Después del snapshot se retira deliberadamente el enlace de imagen en la base
temporal, además de las mutaciones previas de existencias y producto. La fase
check-mutated exige que el enlace realmente haya desaparecido. Después de
pg_restore, check exige recuperar el enlace original y sus metadatos exactos.
verify-quarantine repite la comprobación después del cierre de accesos restaurados.

Archivos cambiados:

- backend/app/tools/inventory_restore_probe.py
- scripts/restore_drill.py
- docs/inventory-release-plan.md
- docs/deliveries/image-metadata-restore-3.50.md

Comandos ejecutados desde la raíz:

```sh
.venv/bin/ruff check backend/app/tools/inventory_restore_probe.py scripts/restore_drill.py
.venv/bin/ruff format --check backend/app/tools/inventory_restore_probe.py scripts/restore_drill.py
python3 -m scripts.restore_drill
```

Resultados reales: Ruff sin errores; seed, mutate, check-mutated, check,
quarantine y verify-quarantine aprobados. El ensayo migra la base temporal al
esquema actual y ejecuta pg_dump/pg_restore reales. Se retiraron sus contenedores,
red, dump y secretos temporales al finalizar. Log local ignorado:
.local/validation/restore-3.50.log.

No hay migraciones nuevas, cambios de interfaz ni acceso al bucket o a la VPS.
No se repiten suites de frontend porque no se modificó el frontend. La aplicación
local conserva su versión ejecutable anterior.

Límite: esto recupera los metadatos de PostgreSQL, no los bytes de fotos en S3.
Siguiente tarea: preparar recuperación verificable de objetos privados y su
coordinación con el respaldo de base, antes de preparar el despliegue HTTPS.
La aceptación manual de productos, movimientos, cámara móvil y permisos continúa
pendiente; el usuario conserva esas pruebas.
