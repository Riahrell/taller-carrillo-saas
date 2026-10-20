# Compras — API administrativa privada (3.20)

Rutas internas sin `/api`; Nginx publica el mismo origen añadiendo `/api`.
No existe ruta pública ni autoridad de empresa/rol desde el cliente.

| Método/ruta interna | Entrada | Resultado |
| --- | --- | --- |
| POST /purchases/suppliers | name | 201 proveedor mínimo |
| GET /purchases/suppliers | limit, after opcionales | Proveedores paginados |
| POST /purchases | supplier_id, lines | 200 compra guardada o repetición |
| GET /purchases | limit, after opcionales | Resúmenes paginados con receipt_id nullable |
| GET /purchases/{id} | UUID | Detalle histórico de compra |
| POST /purchases/{id}/receipt | objeto vacío {} | 200 recepción guardada o repetición |
| GET /purchases/{id}/receipt | UUID | 200 recepción o 204 si aún no existe |

Solo administrador activo de la empresa de sesión. Todas las rutas requieren
X-Expected-Membership canónico y comprobación de cookies/contexto; omitirlo da
428. El valor no elige tenant ni actor. Se verifica proxy real y no se aceptan
cookies de sesión mezcladas con cookies de preautenticación o desafío.
Lecturas exigen intención same-origin (X-Requested-With y política Fetch Metadata),
no confirmación reciente. Escrituras exigen Origin/CSRF/JSON válidos y confirmación
administrativa reciente en servidor antes de commit. Login MFA reciente también
puede constituir esa confirmación; no basta limpiar el campo de reautenticación.

Registrar compra y recibirla requieren un único Idempotency-Key UUID canónico
(428 ausente, 422 inválido/duplicado). SupplierCreation no es idempotente: no
reintentar automáticamente una respuesta incierta; revisar el listado antes de
iniciar otro registro. Compra y recepción se reintentan con idéntica clave y
contenido; el adaptador no genera reintentos.

JSON rechaza campos extra y claves duplicadas, constantes no finitas y precisión
no permitida. No admite tenant, rol, stock, saldo ni totales calculados por cliente.
El costo unitario declarado solo forma parte de la compra autorizada; recepción
no acepta cantidades/costos/precios, los obtiene de esa compra persistida.
Precio de venta no se cambia ni se utiliza como costo. Sin pagos/impuestos nuevos.

POST /purchases admite hasta 32 KiB para las 100 líneas ya definidas. Solo esa
ruta POST tiene el límite ampliado en middleware; Nginx lo aplica a su ubicación
exacta. Proveedores, recepción y demás JSON siguen a 8 KiB; imágenes mantienen
su límite separado. Se controla tanto Content-Length como bytes realmente leídos,
incluida transferencia fragmentada. Proxy con límites de tasa/conexión, buffering
y tiempo de cuerpo; datos sensibles no se añaden al log de rutas.

Paginación: limit 1..100 (25 inicial), entero decimal canónico; after UUID de un
registro existente del listado/empresa actual. Query duplicada/desconocida da 422.
Proveedores orden UUID ascendente; compras por (created_at, id) descendente,
sin offset y con desempate determinista. Cada página incluye items/next_after,
sin permitir seleccionar empresa. No hay filtros comerciales inventados.

DTO de proveedor: id/name/is_active. Resumen de compra: id/supplier_id/
supplier_name/total/created_at/receipt_id. Detalle y recepción usan los DTO
históricos existentes con decimales string; sin empresa, actor persistido,
password, token ni huella interna en el JSON. X-Access-Membership identifica el
contexto autorizado de respuesta; no es autoridad en la próxima petición.
Respuestas y errores no-store. Referencia ajena o inexistente denegada sin
distinguir; una compra ajena nunca produce el 204 de una compra propia pendiente.

Errores seguros: 400 JSON/longitud ambiguos, 401 acceso/contexto denegado, 403 intención/origen rechazados,
409 conflicto de clave o recepción ya hecha, 413 tamaño, 415 tipo, 422 entrada,
428 contexto/confirmación/clave ausentes y 503 indisponibilidad. No exponer
mensajes SQL, cuerpos, cookies o secretos. Se conserva la sesión: estas rutas
no emiten cookies nuevas ni cambian la empresa seleccionada.

Sin migración nueva. Sin pantalla todavía; historial unificado de movimientos
y formularios se conectarán en entregas posteriores.
