# Compras y recepción — contrato incremental

Fuente funcional: Plan Maestro v1.1, secciones 2, 6, 7 y 8; decisiones adoptadas
sobre recepción completa, promedio ponderado y separación de caja/gastos.

## 3.16: entradas y cálculos implementados

`PurchaseCreation` contiene referencia de proveedor y entre 1 y 100 líneas.
Cada `PurchaseLine` contiene referencia de producto, cantidad positiva exacta
(hasta tres decimales) y costo unitario explícito no negativo (hasta seis).
Cero es un costo válido solo cuando se declara. Las colecciones son inmutables.
Un producto aparece una vez por compra: consolidar cantidad antes de enviar;
rechazar duplicados evita dos costos ambiguos para un mismo producto.
El máximo de 100 líneas es un límite técnico inicial, no una regla comercial.

No admite empresa, actor, rol, saldo, precio de venta ni totales del cliente.
Los UUID son referencias, nunca autorización. Esta capa pura no verifica la
existencia/actividad/empresa del proveedor o producto: corresponde al próximo
servicio bajo sesión administrativa vigente. No hay ruta HTTP nueva.

`calculate_purchase` revalida incluso objetos construidos sin validación y
calcula en un contexto Decimal independiente del contexto del llamador.
Para cada línea devuelve cantidad, costo, valoración de entrada a seis decimales
mediante `StockValue.receive`, e importe monetario a dos decimales con HALF_UP.
El importe monetario se calcula directamente desde cantidad × costo, sin volver
a redondear una valoración ya redondeada. Total = suma de importes de línea.
Se rechazan desbordamientos NUMERIC(28,6) y NUMERIC(20,2), nunca se truncan.
No se calculan impuestos, descuentos, transporte ni cargos adicionales.
Estos importes no son una factura fiscal ni un registro de pago.

Ejemplo de doble precisión: 0.001 unidades × 4.999999 dan valoración 0.005000,
pero importe de línea 0.00; redondear la valoración nuevamente daría 0.01 y sería
incorrecto. La valoración del inventario y el importe monetario se conservan
separados. Esta diferencia deberá mantenerse en persistencia y presentación.

## Siguiente: persistencia y recepción autorizada

Agregar proveedores y compras de la empresa con FK compuestas y auditoría.
La creación no debe cambiar existencias. Recepción completa una sola vez, con
clave idempotente, actor vigente y contexto de sesión; bloquear productos/saldos
en orden estable y guardar evidencia, movimientos y saldos en un único commit.
Cantidad/costo proceden de la compra persistida, nunca del comando de recepción.
El promedio nuevo usa cantidad y valoración bloqueadas en servidor. Reintentar
la misma recepción no agrega stock ni cambia su costo histórico otra vez.
No crear pagos, egresos o gastos por recibir mercancía.

Pruebas pendientes de ese servicio: aislamiento de empresa/proveedor/producto,
revocación, confirmación expirada, compra ya recibida, claves repetidas distintas,
dos recepciones simultáneas, rollback entre líneas, capacidad del saldo actual y
reconciliación del libro. No se atribuyen esas garantías al cálculo puro de 3.16.
