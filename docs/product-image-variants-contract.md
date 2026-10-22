# Fotos privadas optimizadas — 3.48

El catálogo solicita 25 productos por página mediante cursor; la API permite
entre 1 y 100 por solicitud. Esta paginación no define una cuota total de productos.
Las fotos de las listas conservan carga diferida y usan ahora la ruta de miniatura.

## Procesamiento

Las fotos nuevas o reemplazadas producen un detalle WebP de hasta 1600 píxeles
y una miniatura WebP de hasta 300 píxeles, sin aumentar imágenes pequeñas.
Calidades 82 y 75, método 4; transparencia conservada y orientación EXIF aplicada.
Se parte de un lienzo limpio: no se conserva GPS, EXIF, ICC, XMP ni texto del
archivo recibido. El archivo original no se persiste en Object Storage.

Se mantienen entrada máxima 8 MiB, 20 millones de píxeles y lado máximo 8192,
formatos reales JPEG/PNG/WebP, rechazo de animaciones y subprocess con memoria,
CPU, tiempo de pared, descriptores y salida acotados. El detalle WebP no supera
4 MiB y la miniatura 256 KiB. Estos son límites, no tamaños promedio prometidos.
El proceso padre valida un sobre binario acotado y las dimensiones/chunks WebP
sin ejecutar Pillow sobre la respuesta del hijo. El proceso tiene límites de
recursos; no se afirma que sea un sandbox completo del sistema operativo.

## Persistencia y publicación

Migración `0047_product_image_variants`: content_type y metadatos inmutables de
miniatura en product_images. Las referencias antiguas reciben tipo PNG y no se
reescriben. Clave de detalle deriva de empresa/producto/UUID internos y formato;
la miniatura deriva del mismo UUID con sufijo `.thumb.webp`. No se guardan URLs
públicas ni campos de autoridad proporcionados por el navegador.

La reserva durable registra hashes, tamaños y dimensiones de ambos objetos
antes de PUT. El puntero vigente y el evento solo cambian después de guardar o
verificar ambos. Un fallo conserva el puntero anterior. Reintentar la misma clave
verifica objetos existentes sin duplicarlos; una versión de procesado incompatible
se rechaza para revisión. Una reserva PNG anterior al despliegue puede terminar
con el procesador PNG previo, sin cambiar su referencia ni hash.

La cuota de fotos de la empresa contabiliza detalle y miniatura, incluyendo
reservas hasta que se limpian. Runtime no puede modificar sus metadatos; conserva
solo los permisos de cambiar estado/puntero. Downgrade rechaza evidencia WebP.

El worker elimina los dos objetos cuando su imagen ya no está vigente; protege
la foto actual. Una eliminación parcial conserva el estado y las referencias
para reintentar, incluidos PUT tardíos. No se borra el original anterior dentro
de la transacción de reemplazo.

## Lectura

Rutas existentes de detalle conservadas. Rutas nuevas de miniatura:

- `/catalog/products/{product_id}/image/thumbnail` para administración.
- `/inventory/availability/products/{product_id}/image/thumbnail` para consulta
  operativa con concesión vigente.

Ambas conservan intención de navegador, membresía esperada, empresa de sesión,
no-store, integridad del objeto y reautorización después de S3. La respuesta es
image/webp para variantes nuevas o image/png para fotos antiguas. GET no transforma
ni crea objetos. No se añaden cachés públicas, CDN o URL permanente que evite la
autorización. Next/Image continúa unoptimized con Blob privado.

El cliente limita la descarga WebP según variante y comprueba MIME, longitud,
firma RIFF/WebP, identidad de imagen y contexto antes de crear el Blob.
El detalle se descarga solo en el flujo de detalle, no en cada miniatura.

## Compatibilidad y límites de entrega

No se hace conversión masiva ni se borran fotos existentes. Las fotos PNG
anteriores siguen disponibles mediante fallback; se optimizan al reemplazarlas.
Una eventual conversión masiva exige su propia tarea con comparación del puntero
vigente, reintentos, evidencia y comprobación de cuotas. No se amplían permisos
de empleados para cambiar fotos ni se instala compresión en el navegador.
