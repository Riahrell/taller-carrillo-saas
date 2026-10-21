# Edición privada y estado del producto

## Alcance

Desde Catálogo, un administrador puede modificar nombre, SKU, categoría, precio
de venta y estado activo/inactivo. No puede enviar tenant, actor, rol, cantidad,
costo, valor, imagen o revisión en el cuerpo. No se eliminan productos ni se
cambian sus identificadores. Las categorías y las fotos mantienen sus propios
flujos; no se introduce edición de categorías en esta entrega.

Desactivar conserva existencias, valor, fotos y evidencias. Las escrituras de
inventario existentes ya exigen producto activo. El SKU sigue reservado en esa
empresa aunque el producto esté inactivo. Se permite conservar una categoría
inactiva ya asignada; no se permite asignar otra categoría inactiva o ajena.

## Contrato HTTP

`GET /catalog/products/{product_id}` conserva su cuerpo actual y añade una
cabecera `ETag: "product-v1-UUID-REVISION"`. La revisión interna no aparece en el
JSON. El identificador de versión no concede autorización.

`PATCH /catalog/products/{product_id}` recibe todos los valores deseados:

```json
{
  "name": "Nombre del producto",
  "sku": null,
  "category_id": null,
  "price": "12.34",
  "is_active": true
}
```

Requiere sesión de administrador, confirmación de identidad vigente según el
modo de acceso, Origin/CSRF, `X-Expected-Membership` e `If-Match` con la versión
revisada. La autoridad procede de la sesión; no de la cabecera ni del JSON.
La respuesta es el producto completo, ETag actual y `X-Access-Membership`,
siempre con `Cache-Control: no-store`.

- 200: transacción confirmada, incluido un envío sin diferencias (sin nueva evidencia).
- 400: precondición mal formada o múltiple; 428: falta versión/contexto o identidad requerida.
- 412: versión antigua; no se sobrescribe ni se reintenta automáticamente.
- 409: SKU ocupado; 422: campos o valores inválidos.
- 403: sesión/permisos/contexto/producto/categoría no autorizados.
- 503 o respuesta perdida: resultado incierto; consultar el producto antes de repetir.

## Consistencia y auditoría

Migración `0040_product_changes`: revisión monotónica BIGINT del producto y
`product_changes` con evidencia tipada antes/después de los cinco campos,
revisiones, actor, empresa, producto y fecha. Sin JSON arbitrario ni secretos.
La revisión evita incluso el caso A → B → A con un formulario antiguo.

El servicio valida de nuevo entradas internas, bloquea empresa, usuario/sesión y
producto, comprueba la versión y la categoría, actualiza únicamente datos de
catálogo e inserta evidencia en la misma transacción. Revalida autorización antes
del commit. Un fallo de evidencia o SKU revierte todo. Restricciones verifican
cambio real, paso de revisión, valores válidos y referencias dentro de la empresa.
El usuario runtime no puede UPDATE/DELETE/TRUNCATE la evidencia.

La migración de bajada rechaza perder evidencia o revisiones utilizadas. No se
promete rollback destructivo de datos ya operativos: se conserva la migración y
se corrige hacia adelante cuando existan cambios registrados.

## Interfaz

Carga versión y categorías propias, confirma identidad, muestra formulario y
revisión, guarda una vez y refresca el catálogo. Las acciones de la lista quedan
inertes durante la edición. El conflicto obliga a recargar y descartar el borrador
explícitamente; el error de SKU conserva los datos para corregirlos. Se comprueba
el contexto tras guardar antes de anunciar éxito. La confirmación no prolonga la
sesión ni modifica el plazo del navegador de confianza.

Pendiente de revisión manual en escritorio/móvil. No se ha agregado una pantalla
para consultar la auditoría de cambios del catálogo; la evidencia queda almacenada.
