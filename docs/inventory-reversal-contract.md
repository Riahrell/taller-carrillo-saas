# Reversa controlada de inventario

## Alcance de esta entrega

Solo el último movimiento de un producto activo generado por un ajuste con
diferencia o una entrada/salida manual. Se conserva el original y se agrega una
compensación con cantidad y valoración exactamente opuestas. El saldo actual
debe coincidir con el saldo posterior del original. Se rechaza si hay otro
movimiento de la misma empresa/producto con fecha igual o posterior, o si ya fue
compensado. Un conteo sin diferencia no tiene movimiento que deshacer.

Es una restricción técnica conservadora: no se intenta recalcular costos de
operaciones posteriores ni inferir su orden si las fechas coinciden. Después de
una reversa, esta no puede deshacerse ni habilita una cadena de reversas antiguas.
Corregir casos más antiguos requiere un conteo real o su flujo documental específico.
No se implementan devolución/anulación de compras, ventas, órdenes ni pagos.
Tampoco se repone físicamente un artículo solo por anular un documento financiero.

El administrador revisa el motivo y la referencia antes de confirmar. La API
obtiene cantidades y valores de la evidencia original, nunca del navegador.

## Contrato HTTP

`POST /inventory/reversals` recibe únicamente:

```json
{
  "product_id": "UUID",
  "source": "COUNT",
  "original_id": "UUID",
  "reason": "Motivo de la corrección"
}
```

`source` admite COUNT/MANUAL. Exige sesión admin vigente, confirmación de identidad,
Origin/CSRF, `X-Expected-Membership` e `Idempotency-Key` UUID canónico. Sin tenant,
rol, precio, cantidades, valores o saldos del cliente.

Respuesta 200: id, product_id, operation_key, original_source, original_id, reason,
before_quantity, after_quantity, before_value, after_value, quantity_delta,
value_delta, created_at. Cantidades con tres decimales, valores con seis. No-store
y `X-Access-Membership`. 422: movimiento no compensable o entrada inválida; 409:
misma clave con otro contenido; 403: acceso/origen/producto no autorizado; 428:
falta contexto/clave/confirmación; 503: resultado no confirmado por persistencia.

## Consistencia

Bloqueos en el orden común empresa → usuario/sesión → producto → saldo. Comparación
del saldo, consulta de movimientos posteriores y escritura bajo la misma
transacción. Evidencia de reversa, nuevo asiento y saldo se confirman juntos. La
valoración original se invierte; no se usa un costo nuevo ni un promedio recalculado.
El saldo queda exactamente en el estado previo documentado y nunca negativo.

Migración 0042: stock_reversals y origen reversal_id en stock_movements. FK
compuesta enlaza empresa, producto, movimiento original y sus deltas. CHECK exige
que la reversa sea el inverso exacto y que sus diferencias coincidan con los
saldos documentados. El asiento compensatorio se enlaza exactamente a esa reversa.
Unicidad del movimiento original y de la clave por empresa. Permisos runtime
append-only. La bajada rechaza borrar evidencia. No cambia registros originales.

La huella canónica incluye producto, tipo/referencia original y motivo normalizado.
Misma clave/contenido devuelve la reversa existente sin escribir otro movimiento,
incluso si el producto se desactivó después. Siempre vuelve a autorizar al actor.
Otra clave para el mismo original no permite repetir la compensación.

## Interfaz e historial

«Deshacer último movimiento» se ofrece únicamente en la primera entrada del
historial más reciente, para COUNT/MANUAL con diferencia. No se ofrece desde una
página antigua, recepción o reversa. El servidor vuelve a comprobar elegibilidad;
el botón no autoriza la operación. Confirmación de identidad, motivo, revisión,
confirmación y respuesta incierta reutilizan el flujo de operación pendiente.
No se solicitan cantidades o costos para la reversa.

El historial unificado admite REVERSAL. Su source_document_id apunta al registro
original de conteo/manual; id identifica la compensación. Original y reversa
siguen visibles. La referencia tipada de paginación admite REVERSAL:UUID.

Mientras la operación queda pendiente se conserva en memoria la misma clave y
cuerpo, ligados a la sesión/empresa. Si se pierde la respuesta, se comprueba esa
operación; no se genera otra clave automáticamente. Una recarga completa pierde
el pendiente en memoria: revisar historial antes de repetir. La unicidad del
original protege de una doble reversa incluso con otra clave.

Pendiente de revisión manual en navegador real. Empleados sin permiso no acceden
a este flujo; no se amplía su autoridad como parte de esta entrega.
