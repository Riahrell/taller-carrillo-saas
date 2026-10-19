# Imágenes de producto — integración prevista

Requisito explícito del propietario: los productos deben admitir imagen y los
archivos se almacenarán en Object Storage de Contabo, proveedor ya contratado.
Este contrato incorpora el requisito; 3.12 añade reprocesado y 3.13 ejecución
limitada en Linux y transporte S3 privado. No hay carga desde la web, rutas HTTP
ni migraciones de imágenes.
La conexión real se verifica separadamente del despliegue en la VPS, aplazado.

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
  3.13 añade `process_product_image`: un subproceso Linux por ejecución, máximo
  uno simultáneo por proceso servidor, sin cola. RLIMIT_AS 512 MiB, CPU 5 segundos,
  archivo 12 MiB, 32 descriptores, core deshabilitado y timeout de pared 10 segundos
  que mata y espera al hijo. Python aislado, entorno sin credenciales heredadas,
  IPC en archivos temporales privados y salida acotada. Los límites de CPU/memoria
  no constituyen sandbox de permisos: conserva UID, filesystem y red del contenedor.
  Dimensionar el contenedor para servidor más hijo; varios procesos multiplican
  la concurrencia. El cuerpo HTTP debe limitarse antes de leerlo y el trabajo
  bloqueante no debe ocupar el event loop. Aún sin ruta pública habilitada.
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
Las credenciales permanecen fuera del repositorio. En 3.13 el smoke explícito leyó
el archivo privado externo sin imprimir valores: cargó un PNG sintético nuevo,
verificó lectura autenticada/hash, rechazo de segunda escritura condicional y
rechazo anónimo HTTP 401. Cada objeto sintético fue eliminado con confirmación
del proveedor. Esto verifica esos objetos y operaciones, no audita todas las
políticas del bucket ni acredita copia de seguridad o integración de la web.

## Transporte 3.13

`PrivateImageStorage` recibe configuración explícita, sin perfil/credenciales
ambientales ni endpoint/bucket predeterminados; HTTPS verificado, firma v4,
path-style, sin proxy ambiental, conexión 3 s, lectura 10 s, un intento SDK.
El timeout de lectura es de socket, no una fecha límite global de transferencia.
PNG privado con Content-MD5, hash propio y `If-None-Match: *`; no degrada estas
garantías si el proveedor no las admite. Lecturas limitadas y verificadas contra
longitud, tipo y hash de la referencia interna, sin URLs públicas o firmadas.

`allocate` genera la referencia opaca **antes** del PUT para que el futuro servicio
la registre duraderamente y pueda recuperar un timeout con resultado incierto.
El transporte no autoriza empresas ni decide borrados: el servicio deberá probar
permisos, reservas y ausencia de referencias. Sin ese servicio y su journal no
habilitar cargas. `delete_unreferenced` es una operación interna, nunca un endpoint.
Prueba operativa: `python -m app.tools.check_product_storage --secret-file RUTA`,
desde backend con entorno instalado. Solo crea objetos sintéticos de UUID nuevos;
no lista ni borra objetos existentes. Exige archivo privado del usuario, sin
interpolación o ejecución de shell. La VPS sigue aplazada.

El reprocesado sigue las recomendaciones de
[Pillow sobre archivos no confiables y metadatos](https://pillow.readthedocs.io/en/stable/handbook/security.html).
No equivale a un escaneo antivirus ni garantiza por sí solo aislamiento de recursos.
Límites de proceso según [Python resource](https://docs.python.org/3.13/library/resource.html);
transporte según [configuración de botocore](https://docs.aws.amazon.com/botocore/latest/reference/config.html)
y [PUT condicional S3](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html),
cuya compatibilidad se comprobó separadamente con Contabo.
