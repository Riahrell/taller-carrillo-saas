# Historial privado unificado de inventario

`GET /inventory/products/{product_id}/history?limit=25&after=COUNT:UUID`

Proyección de solo lectura sobre evidencias existentes: `stock_counts`,
`manual_stock_records`, `purchase_receipt_lines` con su recepción. No usa solo
el libro de movimientos porque un conteo sin diferencia no genera movimiento,
pero sí evidencia. No agrega ni modifica saldo, valor o documentos.

Autorización: administrador vigente de la empresa de la sesión; mismo guard que
los demás GET de inventario, `recent=False`. Verifica lectura del navegador,
contexto esperado si se envía, cookie y permisos actuales. Empresa y actor no se
aceptan como filtros del cliente. Producto o cursor ajeno/inexistente: rechazo
indistinguible 403. Respuestas `Cache-Control: no-store` y cabecera
`X-Access-Membership` ligada al lector autorizado.

Respuesta: `items`, `next_after`. Cada fila contiene exclusivamente:

- `id`, `product_id`, `source`: `COUNT`, `MANUAL`, `RECEIPT` o `REVERSAL`.
- `direction`: `IN`/`OUT` en manuales; `null` en los otros orígenes.
- `source_document_id`: UUID del ajuste/registro manual; UUID de compra en recepción; UUID del registro original en una reversa.
- `recorded_by`: correo actual del usuario de la membresía que creó la evidencia.
  No es un nombre histórico capturado; la membresía original permanece en el
  registro inmutable. No se excluyen actores por estar desactivados actualmente.
- `reason`: motivo original del ajuste/manual/reversa; `null` en recepción porque ese
  documento no tiene un motivo de inventario libre. No se inventa uno.
- `before_quantity`, `after_quantity`, `quantity_delta`: cadenas con tres decimales.
- `before_value`, `after_value`, `value_delta`: cadenas con seis decimales, USD.
- `created_at`: fecha UTC de la evidencia o de su recepción; UI en El Salvador.

Orden descendente por `(created_at, source, id)`. Desempata fechas iguales y UUID
iguales entre tablas sin confundir registros. `after` es `SOURCE:UUID` canónico (incluye REVERSAL);
se resuelve únicamente dentro del producto/empresa autorizado. `limit` entre 1
y 100, por defecto 25; se consulta una fila extra para decidir el siguiente cursor.
El cliente web pide 25 y valida orden, cursor, campos, decimales, diferencias,
producto y membresía. Desconocidos/duplicados en query, cursor mal formado o límite
inválido: 422. No es una instantánea congelada de todo el recorrido: nuevas filas
más recientes se ven al volver a «Movimientos recientes».

Los GET antiguos de conteos y manuales permanecen por compatibilidad. La pantalla
usa un único historial y bloquea su paginación durante edición, fotos u operaciones
pendientes. No promete historial de ventas/órdenes hasta integrar sus documentos.
Sin migraciones de esquema nuevas.
