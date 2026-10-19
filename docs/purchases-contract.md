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

## 3.17: persistencia implementada

Migración `0035_purchases` y modelos `Supplier`, `Purchase`, `PurchaseItem`.
Proveedor mínimo: nombre, estado activo inicial, empresa, autor y fecha UTC.
Sin inventar identificadores fiscales, contactos o condiciones de crédito.
No se impone unicidad del nombre. No hay servicio/HTTP de creación todavía.

Compra conserva proveedor y nombre histórico, actor, fecha, clave de operación
única por empresa, huella SHA-256, cantidad de líneas y total NUMERIC(20,2).
El detalle conserva producto y nombre histórico, posición, cantidad NUMERIC(18,3),
costo/valoración NUMERIC(28,6) e importe NUMERIC(20,2). Las FK compuestas impiden
relacionar proveedor, actor, compra o producto de otra empresa.
Posición 1..100 y producto únicos por compra; línea con UUID estable para
futura evidencia de recepción. Las comprobaciones DB exigen valoración e importe
iguales a los productos redondeados independientemente a seis y dos decimales.

Los CHECK no comprueban sumas ni cantidad de filas entre tablas. El servicio
pendiente deberá insertar cabecera y líneas, comprobar suma y cantidad, y capturar
los nombres autorizados en la misma transacción. SQL directo con el rol runtime
no está aislado por RLS ni protege contra toda escritura arbitraria. PostgreSQL
puede redondear escala de entradas SQL: el contrato exacto de aplicación es
obligatorio antes de persistir. No confundir FK con autorización de lectura.

Runtime solo lee/inserta estas tablas: sin UPDATE, DELETE ni TRUNCATE. Hasta
incorporar un caso de uso auditado no se habilita modificar el proveedor.
La compra registrada es inmutable; el estado de recepción se añadirá como
evidencia separada, no reescribiendo costos/importes. No hay borrado en cascada.
Downgrade bloquea tablas y se niega si cualquiera tiene datos, incluso un
proveedor sin compras. La migración vacía sí puede revertirse y reaplicarse.

No se modificó stock_movements, stock_balances, caja o gastos. Registrar una
compra no equivale a recibirla. La recepción tipada y su origen en el libro
se implementarán y probarán antes de habilitar cambios de existencias.
