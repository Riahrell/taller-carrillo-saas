# Salidas operativas autorizadas — 3.45

Decisión de alcance: perfil Cajero activo con concesión explícita de salidas. No se convierte Solo lectura en escritura, ni se amplía Mecánico (que requiere órdenes asignadas). El administrador cambia el perfil desde Membresías cuando corresponde y concede desde Permisos. Las entradas, ajustes, costos, compras y anulaciones siguen administrativos. Esta concesión es independiente de ventas/cobros.

`GET/PATCH /auth/memberships/{membership_id}/inventory-withdrawal` exige administrador actual y empresa de sesión. PATCH exige confirmación de identidad vigente, CSRF/origen, membresía esperada e If-Match `"inventory-withdrawal-v1-{target}-{revision}"`. JSON estricto `{ "granted": boolean }`. No-op no crea evidencia. Una revisión desactualizada devuelve 412. No se concede a otros perfiles o accesos inactivos. UUID ajeno/inexistente no distingue datos.

La concesión se conserva en `inventory_withdrawal_grants`: historial append-only, autor/objetivo ligados a la misma empresa por FK, revisiones únicas y antes/después. Runtime no puede actualizar, borrar ni truncar; downgrade rechaza evidencia existente. Cambio de perfil o desactivación retira concesiones en la misma transacción y reactivar no las restaura.

La consulta operativa existente permite ahora a Cajero con esta concesión consultar productos activos, fotos, precio de venta y cantidades. Cada petición revalida estado y permiso actuales. No necesita ni utiliza una concesión Solo lectura. La descarga de fotos revalida también después del acceso a almacenamiento.

`POST /inventory/availability/withdrawals` recibe únicamente `product_id`, `quantity` decimal exacto positivo con hasta tres decimales, y `reason` (1–500 caracteres útiles). No admite dirección, costo, precio, tenant o saldo del cliente. La dirección OUT la fija el servicio. Requiere sesión Cajero con concesión actual, empresa activa, membresía esperada, CSRF/origen e Idempotency-Key UUID.

Reutiliza el núcleo transaccional de movimientos: empresa → sesión → producto activo propio → saldo; cálculo decimal, salida sin saldo negativo, valoración media calculada en servidor, movimiento/evidencia/saldo en el mismo commit. Fallo de persistencia revierte todo. La idempotencia compara fingerprint normalizado y además exige mismo autor en este flujo. Otra petición con la misma clave/datos no vuelve a descontar; otro contenido devuelve 409. Revocar también impide repetir una operación anterior. Las rutas administrativas mantienen su autorización original.

La respuesta operativa incluye solo id, product_id, operation_key, before_quantity, after_quantity, quantity_delta, reason y created_at; contexto en cabecera. No serializa costos, valores, fingerprint o identificadores de otras cuentas. El administrador puede revisar la evidencia en su historial existente.

En `/inventario/consulta` Cajero dispone de cantidad/motivo, revisión y confirmación. Respuesta incierta no se repite automáticamente: se bloquea la pantalla y exige revisar existencias e historial administrativo antes de repetir. El cliente no mantiene un outbox persistente de operaciones. La revocación rige en la siguiente operación serializada; no borra datos observados anteriormente.
