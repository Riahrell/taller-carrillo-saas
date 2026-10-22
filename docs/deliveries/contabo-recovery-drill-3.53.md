# 3.53 — Ensayo real de recuperación en bucket separado

El propietario aportó el bucket privado taller-carrillo-restauracion y su archivo
de configuración externo. Confirmó que las claves tienen alcance sobre todo el
Object Storage e indicó expresamente no acceder al bucket de imágenes reales.
Se respetó ese alcance: todas las operaciones S3 y la petición anónima apuntaron
únicamente al bucket de restauración; no se listó ningún bucket ni se inspeccionó
la VPS o la base local de desarrollo.

## Implementación

`restore_drill` admite opt-in con configuración privada, bucket esperado,
bucket prohibido y endpoint esperado. Los parámetros externos parciales se
rechazan antes de crear recursos. El secreto se monta temporalmente dentro del
proyecto CI aislado y nunca se imprime. La base exige APP_ENV=test, host db,
carrillo_test y carrillo_owner. El cliente S3 se construye únicamente después
de comprobar destino, base y las dos claves aleatorias propias del ensayo.

La fixture puede generar WebP real de 640×480 con miniatura mediante el mismo
procesamiento del producto. No altera el comportamiento de la fixture antigua.
El ensayo conserva las verificaciones de libro/saldo/reversa y cuarentena de
accesos. La limpieza solo considera esas dos claves y comprueba integridad antes
de borrar. Solo HTTP 404 confirma ausencia; 403/500 no demuestran pérdida.

Archivos completos cambiados:

- scripts/restore_drill.py
- scripts/tests/test_restore_drill.py
- backend/app/tools/storage_restore_probe.py
- backend/app/tools/inventory_restore_probe.py
- backend/app/tools/restore_probe.py
- backend/tests/test_storage_restore_probe.py
- docs/operations/private-image-backup.md
- docs/inventory-release-plan.md
- docs/deliveries/contabo-recovery-drill-3.53.md

## Comandos y resultados ejecutados

```sh
PYTHONPATH=backend .venv/bin/pytest backend/tests/test_storage_restore_probe.py backend/tests/test_image_archive.py -q
python3 -m unittest scripts.tests.test_restore_drill
.venv/bin/ruff check scripts/restore_drill.py scripts/tests/test_restore_drill.py backend/app/tools/storage_restore_probe.py backend/app/tools/inventory_restore_probe.py backend/app/tools/restore_probe.py backend/tests/test_storage_restore_probe.py
.venv/bin/ruff format --check scripts/restore_drill.py scripts/tests/test_restore_drill.py backend/app/tools/storage_restore_probe.py backend/app/tools/inventory_restore_probe.py backend/app/tools/restore_probe.py backend/tests/test_storage_restore_probe.py
python3 -m scripts.restore_drill \
  --storage-config /home/riahrell/.config/taller-carrillo/contabo-s3-restauracion.env \
  --expected-bucket taller-carrillo-restauracion \
  --forbidden-bucket taller-carrillo-imagenes \
  --expected-endpoint https://usc1.contabostorage.com
```

Resultados reales del 22 de octubre de 2026:

- 17 pruebas pytest aprobadas y 1 unittest aprobado (4 opciones parciales).
- Ruff correcto; construcción del backend y migraciones en PostgreSQL temporal.
- Subida y lectura verificada de dos objetos sintéticos; acceso anónimo denegado.
- pg_dump completo y archivo privado de ambas variantes correspondientes.
- Borrado intencional de esos objetos y ausencia confirmada mediante HTTP 404.
- Mutación de base confirmada antes de pg_restore; restauración y conciliación
  de producto, saldos, asientos, reversa, mínimo y metadatos correctas.
- Restauración real de ambos objetos, coincidencia exacta con las referencias
  SQL, hashes y decodificación WebP correctos.
- Repetición de la restauración sin sobrescritura: correcta.
- Cuarentena de accesos y comprobaciones posteriores: correctas.
- Limpieza de los dos objetos sintéticos: ausencia confirmada; base, redes,
  contenedores, dump, archivo y secreto temporal retirados al terminar.

Log privado ignorado: .local/validation/contabo-restore-3.53.log.

## Límites y siguiente tarea

Demuestra el recorrido con PostgreSQL temporal y Contabo real, usando únicamente
contenido sintético. No es un respaldo de datos reales ni prueba restauración de
la VPS, claves operativas, volumen de producción, retención prolongada o copia
externa cifrada. No se modificaron dependencias, esquema ni interfaz de la app.
Los servicios locales se mantienen ejecutables sin necesidad de reinicio.

Sigue preparar una configuración revisable del despliegue HTTPS y su recuperación,
con inventario actualizado de la VPS y aceptación manual del propietario. Antes
de sustituir el despliegue anterior se requiere respaldo recuperable de ese
sistema, identificado separadamente de este ensayo sintético.
