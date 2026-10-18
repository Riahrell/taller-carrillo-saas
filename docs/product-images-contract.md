# Imágenes de producto — integración prevista

Requisito explícito del propietario: los productos deben admitir imagen y los
archivos se almacenarán en Object Storage de Contabo, proveedor ya contratado.
Este contrato incorpora el requisito; 3.12 implementa únicamente el procesador
local de archivos, todavía sin carga, rutas HTTP ni migraciones.
La conexión real se hará separada del despliegue en la VPS, actualmente aplazado.

## Almacenamiento y aislamiento

- Adaptador S3 compatible en backend, con endpoint, región y bucket configurados
  en servidor. No deducir endpoint de la VPS ni codificar una región supuesta.
- Bucket y objetos privados. Claves fuera del repositorio y del navegador;
  permisos limitados al almacenamiento necesario y verificados con Contabo antes
  de conectar producción. No prometer compatibilidad total con políticas de AWS.
- El servidor genera claves opacas e inmutables y obtiene empresa y producto de
  una sesión autorizada. Un prefijo por empresa ayuda a organizar, pero no
  sustituye autorización. No aceptar rutas, URLs remotas ni claves elegidas por
  el cliente; tampoco descargar imágenes desde URLs proporcionadas.
- Primera versión: lectura por backend autorizado, sin URLs permanentes públicas.
  El catálogo público tendrá autorización de publicación explícita e independiente.
  Asociar una imagen interna no publica automáticamente producto ni archivo.

## Modelo objetivo y consistencia

Relación `product_images`, separada del registro comercial y de las futuras
galerías públicas: identificador servidor, empresa, producto, clave de objeto,
tipo verificado, tamaño, dimensiones, hash de integridad y fecha. FK compuesta
empresa/producto; ninguna URL firmada ni secreto se persiste como referencia.
La primera interfaz permitirá una imagen principal; el detalle físico de la
selección y los estados se cerrará con la migración, sin modificar aún los DTO.
La disponibilidad de imagen no alterará precio, stock o valoración.

PostgreSQL y S3 no comparten transacción. Cargar y validar un objeto nuevo antes
de cambiar la referencia visible; asociar y auditar en una transacción de DB con
revalidación del actor. Reemplazar mediante clave nueva, nunca sobrescribir el
objeto vigente. Si falla la asociación, conservar la imagen anterior y programar
limpieza del objeto huérfano. Borrado diferido e idempotente después del commit,
con comprobación de referencias. Definir recuperación de interrupciones y cuotas
antes de habilitar cargas. Un fallo del almacenamiento no debe eliminar stock.

## Validación y pruebas exigidas antes de habilitar

- Autorizar altas/cambios con las guardias administrativas existentes, CSRF y
  contexto vigente; verificar lectura y escritura con producto de otra empresa,
  sesión revocada y rol insuficiente.
- Límites configurados de bytes, píxeles, duración y concurrencia; verificar el
  contenido decodificado, no confiar en extensión o Content-Type declarado.
  Primera versión de imágenes raster estáticas; rechazar SVG y archivos animados.
  Reprocesar a formato de salida permitido y retirar metadatos antes de guardar.
  El procesador 3.12 acepta JPEG, PNG y WebP estáticos según bytes reales, máximo
  8 MiB, 20 millones de píxeles y 8192 píxeles por lado antes de decodificar.
  Verifica estructura y decodificación completa; aplica orientación EXIF, reduce
  proporcionalmente a 1600 píxeles por lado sin ampliar y genera PNG RGBA limpio,
  máximo 12 MiB, conservando transparencia y eliminando metadatos. Son límites
  técnicos iniciales, no cuotas comerciales. No realiza gestión de color ICC.
  El límite de bytes del cuerpo HTTP debe aplicarse antes de materializarlo.
  Todavía requiere worker aislado con memoria/tiempo y admisión de concurrencia;
  **no llamar este procesador desde una ruta pública ni un hilo de FastAPI**.
- Probar archivo inválido/truncado, exceso de tamaño/píxeles, sustitución
  concurrente, reintento, rollback, timeout de S3 y limpieza sin borrar imágenes
  referenciadas. Pruebas locales con adaptador simulado y prueba real aislada en
  Contabo antes de declarar integración operativa.
- No registrar credenciales, cuerpos binarios ni URLs firmadas. Respaldar DB y
  objetos con procedimiento conjunto; el bucket no equivale a una copia de seguridad.

## Referencias verificadas

Contabo documenta compatibilidad S3 y configuración por endpoint/credenciales:
https://docs.contabo.com/docs/storage/object-storage/
https://help.contabo.com/en/support/solutions/articles/103000411092-object-storage-s3-compatibility-protocols-and-connection-settings

## Configuración comunicada por el propietario

El propietario confirmó endpoint `https://usc1.contabostorage.com`, bucket exclusivo
`taller-carrillo-imagenes`, direccionamiento path-style y región `default`.
Indicó que el bucket es privado y que probó su conexión. Son datos comunicados,
todavía no comprobados por esta implementación. Se introducirán mediante
configuración del servidor, sin convertirlos en valores predeterminados del SaaS.
Las credenciales permanecen fuera del repositorio y no se han leído ni copiado
en esta fase. No se conectó la cuenta, transfirió un archivo ni verificó la
política real del bucket durante 3.12.

El reprocesado sigue las recomendaciones de
[Pillow sobre archivos no confiables y metadatos](https://pillow.readthedocs.io/en/stable/handbook/security.html).
No equivale a un escaneo antivirus ni garantiza por sí solo aislamiento de recursos.
