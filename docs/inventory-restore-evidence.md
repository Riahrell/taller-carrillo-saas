# Ensayo de restauración con inventario — entrega 3.39

Se amplía el ensayo existente `python3 -m scripts.restore_drill` con un grafo
sintético de producto editado, auditoría de catálogo, conteo, entrada manual,
reversa, libro de movimientos, saldo y mínimo configurado. La prueba conserva
además el factor cifrado y los códigos de recuperación que ya verificaba.

## Secuencia probada

1. Crear PostgreSQL temporal con esquema completo, sin volúmenes operativos.
2. Sembrar datos sintéticos bajo el guard del comando: APP_ENV=test, host=db,
   database=carrillo_test y usuario carrillo_owner. El grafo se escribe como
   fixture propietaria; este ensayo no demuestra autorización del servicio.
3. Obtener pg_dump completo en formato custom y archivo privado temporal.
4. Alterar deliberadamente los datos sintéticos posteriores al snapshot:
   producto/revisión, saldo materializado y nuevo mínimo. Consumir también otro
   código de recuperación. Verificar que la mutación realmente ocurrió.
5. Restaurar mediante pg_restore --clean --if-exists --no-owner --exit-on-error.
6. Verificar que vuelven los datos del snapshot: producto/revisión/auditoría,
   mínimo original, tres asientos de inventario, referencias de la reversa y saldo.
   La suma de unidades y valores del libro coincide exactamente con ese saldo.
7. Verificar descifrado del factor y estado de los códigos; ejecutar cuarentena
   post-restauración y volver a verificar seguridad e inventario.
8. Retirar contenedores, redes, archivos temporales y credenciales sintéticas.

Resultado ejecutado: todas las fases aprobadas. La restauración completa conserva
las referencias cíclicas de libro/reversa añadidas por la migración 0042.
Comprobación positiva de la mutación antes del restore evita probar solo datos
que nunca cambiaron. No se publica el contenido de dumps, claves ni datos de prueba.

El helper de inventario no tiene endpoint ni CLI público; se invoca únicamente
desde el comando ya restringido a la base temporal. Las credenciales operativas no
se leen, exportan o modifican en este ensayo.

## Límites

Esto demuestra restauración de PostgreSQL de la aplicación nueva con datos
sintéticos. No valida la copia antigua de Contabo, recuperación de una VPS,
objetos/fotos en S3, DNS, certificados ni procedimientos humanos de emergencia.
El despliegue remoto continúa aplazado. Se requiere además política y recuperación
del almacenamiento de objetos antes de declarar un respaldo integral operativo.
