# Movimientos manuales de inventario

Implementa IN/OUT del itinerario de inventario. No emite ventas, pagos, compras
financieras ni movimientos atribuidos a órdenes inexistentes.

## Escritura

`POST /inventory/movements`, sesión opaca y CSRF del mismo origen.
Exige `X-Expected-Membership` e `Idempotency-Key` UUID canónico.
El JSON admite exclusivamente:

- `product_id`: producto de la empresa resuelta por la sesión.
- `direction`: `IN` o `OUT`.
- `quantity`: unidades positivas, exactas, hasta tres decimales; nunca saldo final.
- `reason`: motivo obligatorio, normalizado, máximo 500 caracteres.
- `unit_cost`: costo exacto por unidad, hasta seis decimales, obligatorio para
  entrada y nulo/ausente para salida. La salida usa la valoración del servidor.

Se aplican los límites NUMERIC existentes. Se rechazan floats, cantidades cero,
negativos, infinito, overflow, campos adicionales y costo aportado a una salida.
No admite empresa, rol, saldo anterior, valor calculado o precio de venta.

La respuesta 200 contiene los campos del comprobante de conteo existente más
`direction`: identificadores de evidencia/producto/operación, saldos y valores
antes/después, deltas, motivo, costo declarado y fecha. La autoridad actual de
la respuesta se enlaza mediante `X-Access-Membership`; el actor original queda
registrado por el servidor en evidencia inmutable, no tomado del JSON.

Misma clave y contenido devuelve el comprobante original sin duplicar.
Contenido distinto con esa clave devuelve 409. Toda repetición vuelve a autorizar
la sesión y sus permisos actuales. Insuficiencia/valor fuera de límites devuelve
422; fallo de persistencia devuelve 503 sin publicar éxito. En incertidumbre se
conserva la misma clave y el mismo cuerpo; nunca se crea una clave nueva para
repetir automáticamente una escritura de resultado desconocido.

## Lectura

`GET /inventory/products/{product_id}/manual-movements?limit=25&after=<UUID>`
responde `items` y `next_after`, orden descendente por fecha e ID.
El cursor se restringe al mismo producto y empresa. Mismos controles privados y
cabeceras sin caché que los conteos. La UI muestra las entradas/salidas por
separado de las correcciones por conteo; el historial unificado sigue pendiente.

## Consistencia

Transacción única: autorización actual, bloqueo de empresa/usuario/sesión,
producto y saldo; evidencia manual, apunte de libro y saldo confirmado.
Entrada: cantidad sumada y costo explícito acumulado. Salida: costo ponderado del
saldo bloqueado, redondeando el valor total una vez a seis decimales; retirar todo
el stock deja cantidad y valor cero. No hay cambio del precio de venta.

La migración 0039 agrega `manual_stock_records` y la tercera fuente tipada del
libro. Un apunte tiene exactamente una fuente y una FK compuesta garantiza
empresa, producto, cantidades y valores coincidentes con ella. La evidencia
comprueba saldos/deltas/valoración; el rol de aplicación no puede actualizar,
borrar ni truncar esa evidencia. La bajada rechaza destruir historial existente.

## Permisos y límites de esta entrega

Se conserva el administrador como único perfil autorizado en inventario.
El registro autónomo y los permisos específicos de empleados todavía no están
implementados. No hay reversa automática de movimientos en esta entrega; no se
borra historial ni se presenta una corrección por conteo como reversa.
