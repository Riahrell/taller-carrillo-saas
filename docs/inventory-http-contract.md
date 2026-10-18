# Inventario privado — contrato HTTP 3.10

Rutas públicas a través del proxy `/api/inventory`; FastAPI recibe `/inventory`.
Operaciones administrativas de la empresa elegida en sesión. No son catálogo
público, ventas, pagos ni edición libre de stock. 3.11 conecta la pantalla privada
según `inventory-ui-contract.md`. El propietario delegó las decisiones técnicas; estas rutas conectan
el conteo físico ya definido, sin añadir reglas comerciales nuevas.

Separación confirmada con el propietario: área privada de inventario prevista en
`panel.taller-carrillo.com/inventario` y vitrina pública de productos en
`taller-carrillo.com/productos`. Son direcciones de diseño, sin despliegue en esta
entrega. La ruta `/catalogo` existente continúa siendo administración privada.
Alta interna y publicación pública son decisiones distintas: no publicar productos
automáticamente ni reutilizar estos DTO con costos/saldos en la vitrina. El futuro
catálogo público identificará la empresa desde configuración autorizada en servidor.

| Método y ruta detrás de `/api/inventory` | Entrada | Resultado |
| --- | --- | --- |
| GET `/products/{product_id}/balance` | UUID de producto | producto, cantidad, valor y costo unitario promedio |
| GET `/products/{product_id}/counts` | `limit` 1–100, predeterminado 25; `after` UUID opcional | `items`, `next_after` |
| POST `/counts` | producto, cantidad física final, motivo, costo declarado opcional | evidencia del conteo confirmado |

## Autoridad y transporte

- Cookie de sesión válida, sin cookies mezcladas de preautenticación/desafío,
  peer proxy confiable y dirección reenviada verificada. Nunca empresa/rol en JSON.
- GET exige intención de navegador y la política existente de origen/Sec-Fetch-Site;
  admin actual, sin exigir confirmación reciente. Membresía esperada opcional
  compara contexto, no lo selecciona. Lecturas no renuevan la confirmación.
- POST exige origen/CSRF, `X-Expected-Membership` y `Idempotency-Key`. Esta última
  debe ser un único UUID canónico minúsculo, conservado junto al mismo contenido
  ante un resultado incierto. No es una credencial ni elige empresa.
- POST revalida admin/confirmación reciente dentro de su transacción y antes de
  commit. JSON con campos extra, duplicados o formatos no exactos se rechaza.
  El cliente declara el conteo deseado; saldo previo, diferencias y valoración
  resultante son del servidor. No acepta costo declarado para alterar el histórico.
- Límite JSON 8 KiB tanto en middleware como proxy, con tiempo de cuerpo 10 s y
  límites existentes de solicitudes/conexiones. Sin llamadas a Object Storage
  durante una transacción de conteo; las imágenes tendrán su flujo independiente.

## Semántica y respuesta

POST devuelve 200 tanto al registrar como al repetir el mismo conteo: la respuesta
es evidencia confirmada, no una indicación de que se ejecutó otro movimiento.
Incluye `id`, `product_id`, `operation_key`, cantidades y valores anteriores y
resultantes, diferencias firmadas, motivo, costo declarado cuando aplique y fecha
original UTC. No incluye empresa, autor, huella ni datos de acceso. El autor
original se conserva en la evidencia, aunque otro admin autorizado la consulte.

Cantidad JSON siempre string de tres decimales; valoración/costo string de seis.
`average_unit_cost` se calcula en servidor como valor/cantidad, redondeado una sola
vez a seis decimales con HALF_UP para consulta. Es null sin existencia, no un costo
cero supuesto. Es una presentación del promedio: no se reutiliza redondeado para
valorar salidas. Con existencia valorada explícitamente en cero, el promedio es
cero. El precio de venta pertenece al catálogo y se consulta separado; no altera
la valoración de inventario. La ficha deberá reunir imagen, nombre, SKU, categoría,
estado, cantidad, costo unitario promedio, precio de venta, valor total e historial.
Imagen en Object Storage Contabo y ficha visual siguen pendientes. Los costos son
datos internos sujetos a permisos; no se publican en el catálogo público.
La respuesta inicial y la repetición desde DB usan la misma representación, sin
floats. El saldo de producto existente sin movimientos se presenta como cero sin
crear filas al leer. Productos inactivos conservan lecturas/historia; nuevas
operaciones requieren producto activo. Cantidad igual conserva evidencia sin
movimiento y sin revaluación. Las lecturas no crean eventos comerciales.

Todas las respuestas de estas rutas llevan `Cache-Control: no-store`; éxitos
incluyen `X-Access-Membership` del actor actual. No emiten cookies ni ETag.
No hay URL pública ni exposición de costos a perfiles de taller sin permiso admin.

## Historial y paginación

Conteos más recientes primero por `(created_at, id)` descendente. Cursor UUID
resuelto por servidor dentro de empresa/producto; ausente, ajeno o de otro producto
se deniega de forma genérica. No se acepta timestamp ni tenant del cliente.
Se leen como máximo `limit + 1` registros; `next_after` es el último UUID devuelto
si hay más. El historial incluye conteos sin diferencia, no solo movimientos.
Es paginación viva, no un snapshot de varias peticiones; nuevas inserciones pueden
aparecer al volver al inicio. La evidencia es inmutable para el runtime.

Índice `ix_stock_counts_history` empresa/producto/fecha/id, migración
`0033_stock_history`, para resolver este recorrido sin ordenar todo el historial.
Las consultas mantienen inicialmente el bloqueo de empresa de la guardia común;
la consistencia prima sobre paralelismo, con límites de bloqueo/sentencia existentes.

## Errores sin detalles internos

401 sesión ausente/inválida/revocada; 403 permiso/contexto/recurso/peer/origen/CSRF;
428 falta de contexto/clave o confirmación reciente; 409 misma clave con contenido
diferente; 422 entrada/consulta/clave/conteo inválido; 400 JSON ambiguo; 415 tipo de
contenido; 413 cuerpo excesivo; 429 cuota de proxy; 503 persistencia o upstream.
Los errores no devuelven actor ni trazas SQL. Referencias de producto ajenas o
ausentes comparten denegación. Proxy normaliza 502/503/504 a JSON 503 y omite query,
cuerpos y cabeceras privadas de sus logs; error log deshabilitado para la ubicación.

Ante 503 o corte de conexión no asumir rollback: un commit puede haber terminado.
Repetir solo tras recuperar el contexto autorizado, con misma clave y contenido;
no cambiar la clave automáticamente. Un 409 requiere revisar la operación original.

## Evidencia requerida

Pruebas HTTP con login real contraseña/TOTP y PostgreSQL; saldos sin escrituras al
leer; respuesta inicial/repetida idéntica; paginación con empates y cursor ajeno;
perfiles no admin, sesión revocada, confirmación caducada y peer directo; campos de
autoridad rechazados; errores seguros. Prueba adicional por Nginx/Uvicorn real con
conteo, repetición, saldo, historial, recurso ajeno y límite de cuerpo. Resultados
ejecutados se documentan en `deliveries/inventory-3.10.md`.
