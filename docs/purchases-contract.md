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

## 3.18: registro administrativo implementado

`PurchasesAdministrator.create_supplier` exige sesión administrativa vigente,
membresía esperada y confirmación reciente. Valida nombre y guarda autor/fecha
como evidencia de creación inmutable. Sin vincular o deduplicar por nombre.
Esta alta no tiene clave idempotente: una respuesta perdida no autoriza repetir
automáticamente; el futuro adaptador deberá permitir revisar antes de crear otro.

`record_purchase` recibe compra tipada y UUID de operación. Empresa y actor
proceden exclusivamente de sesión, nunca del payload. Revalida copias internas,
bloquea empresa/usuario/sesión y productos en orden UUID; comprueba proveedor y
productos activos de la empresa. El proveedor no se bloquea con FOR UPDATE:
es inmutable para runtime y la empresa es el punto común de serialización de
escrituras autorizadas. No se amplían permisos SQL para poder bloquearlo.

Calcula importes, captura nombres y guarda cabecera/detalles en un commit.
La compra inmutable con actor/fecha y líneas constituye evidencia tipada de su
creación; no se introduce un payload libre ni se reescribe audit_events.
Antes de devolver vuelve a comprobar la autorización y la confirmación reciente.
No devuelve éxito antes de commit. Falta de capacidad produce PurchaseInvalid;
fallo SQL/commit produce PurchasesUnavailable sin detalles del driver.

La huella SHA-256 incluye versión, proveedor y líneas en su orden original, con
decimales canónicos: 2 y 2.000 equivalen, reordenar líneas es contenido distinto.
Clave igual/contenido igual retorna la evidencia original, incluso si después se
renombra/desactiva el producto o proveedor; contenido distinto genera conflicto.
Toda repetición exige autorización reciente. No recalcula costos históricos con
catálogo actual ni duplica registros. Otro administrador autorizado de la misma
empresa puede recuperar la evidencia; el actor persistido original se conserva.

`current_purchase` consulta por empresa de sesión y UUID; admite referencias
inactivas históricas y no exige confirmación reciente para leer. Sigue exigiendo
rol admin y membresía esperada. Cabecera sin detalle, conteo/total incoherentes,
posiciones incompletas o importes inválidos fallan cerrados en lectura/repetición.
DTO excluye empresa y actor interno; decimales se serializan como strings a la
escala correspondiente. Actor de respuesta interno representa al consultante.

Sin nuevas migraciones, rutas HTTP ni UI. La recepción, stock, pagos y gastos no
se ejecutan desde este servicio. No hay reintentos automáticos ni llamadas de red.

## 3.19: recepción completa implementada

`PurchaseReceiver.receive` acepta sesión, membresía esperada, compra y UUID de
operación; no recibe cantidades, costos o saldo. Obtiene compra y detalle
inmutables de la empresa de sesión, valida su coherencia y exige administrador
con confirmación reciente antes de escribir y de confirmar. Bloquea empresa,
usuario/sesión y productos en orden UUID; el módulo inventario bloquea los saldos.
La empresa serializa también la creación del primer saldo.

Compra recibida admite exclusivamente repetir su clave original con la misma
compra; otra clave produce conflicto. Una clave aplicada a otra compra también
produce conflicto. Repetición exige permiso reciente pero conserva snapshots
originales, incluso si el producto se desactiva o su saldo cambia después. No
revive existencias. Una recepción nueva exige productos activos. Un proveedor
desactivado no invalida una compra ya registrada: no se elige otro proveedor ni
se crea una compra nueva al recibirla. Esta decisión permite cerrar su entrega
física y no habilita nuevas compras con el proveedor inactivo.

`inventory/receiving.py` aplica cantidades/costos de cada línea persistida mediante
StockValue.receive; guarda snapshots anteriores/resultantes, movimiento y saldo.
No controla commit ni autoriza por sí solo: se ejecuta únicamente dentro de la
transacción autorizada de recepción. Si cualquier producto excede capacidad o
falla un INSERT/UPDATE/commit, toda la recepción revierte. Sin pagos ni gastos.

Migración `0036_purchase_receipts`:

- `purchase_receipts`: empresa/compra única, empresa/clave única, actor y fecha UTC.
- `purchase_receipt_lines`: una por línea de compra, referencias compuestas a su
  recepción y compra/producto; FK incluye cantidad y valoración exactas de origen.
  Conserva antes/después y deltas positivos de cantidad y no negativos de valor.
- `stock_movements`: origen excluyente conteo o línea de recepción. Cada línea
  puede generar un solo movimiento; FK incluye empresa/producto y ambos deltas.
  Los conteos anteriores se conservan; no hay backfill ni cambio de existencias.
- Runtime sin UPDATE/DELETE/TRUNCATE de recepción y detalles. Downgrade rechaza
  pérdida de cualquier recepción; en esquema sin recepciones restaura el contrato
  anterior, incluso conservando conteos existentes.

La recepción completa es una garantía del servicio transaccional: no hay CHECK
que cuente filas de varias tablas. Una repetición con líneas o movimientos
incompletos falla cerrada, sin generar reemplazos automáticamente. El DTO excluye
empresa y actor interno y serializa cantidad a tres decimales/valor a seis.
Las FK y permisos no constituyen aislamiento RLS frente a SQL arbitrario.

Todavía sin HTTP/UI de compras o recepción. La consulta existente de conteos sigue
mostrando conteos; deberá añadirse lectura del historial con origen de compra al
conectar esta funcionalidad al panel.

## 3.20: HTTP y listados

Rutas privadas conectadas según `purchases-http-contract.md`. Listados limitados
por empresa/cursor; resumen de compra incorpora receipt_id nullable derivado en
servidor. Lectura de recepción devuelve evidencia original o ausencia (204),
siempre comprobando primero compra y contexto autorizados. Confirmar recepción
solo admite objeto vacío y UUID de operación; no modifica el contrato de costos.
