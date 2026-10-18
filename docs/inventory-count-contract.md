# Conteo físico — contrato de implementación

Aplica las decisiones de inventario existentes, sin movimientos libres enviados
por cliente. La primera operación será un conteo administrativo de un producto
activo de la empresa de sesión. No representa compra, venta ni pago.

## Entrada y valoración implementadas

`PhysicalCount`: producto, cantidad física final exacta, motivo obligatorio de
hasta 500 caracteres y costo unitario declarado opcional de hasta seis decimales.
El límite de motivo es técnico. Campos adicionales se rechazan; no admite empresa,
actor, saldo anterior, diferencia, valor resultante ni precio de venta.

`StockValue.recount` obtiene el resultado usando un saldo que deberá leer y bloquear
el servicio. Menor cantidad consume valor al promedio histórico; cero consume todo.
Mayor cantidad usa la razón histórica valor/cantidad sin redondear primero el costo
unitario. Si cantidad o valor previo es cero, exige costo declarado explícito,
incluido cero cuando el administrador lo declare. Rechaza costo declarado cuando
corresponde el histórico y cuando no hay entrada. Conteo igual conserva exactamente
cantidad y valor: debe guardar evidencia, sin generar movimiento ficticio.

Los componentes de entrada/valoración son puros. El servicio 3.9 descrito abajo
los conecta con autorización y persistencia. 3.10 agrega HTTP privado según
`inventory-http-contract.md`, todavía sin formulario de stock.

## Persistencia implementada en 3.8

Migración `0032_stock_counts`, aplicada únicamente en las bases efímeras de
validación. Sin backfill, datos demo ni modificación de existencias operativas.

- `stock_balances`: clave única empresa/producto, cantidad NUMERIC(18,3) y valor
  NUMERIC(28,6), no negativos y finitos; cantidad cero implica valor cero.
  FK compuesta a producto de la misma empresa.
- `stock_counts`: evidencia inmutable con empresa/producto, actor de membresía
  de esa empresa, cantidad/valor anteriores y resultantes, motivo, costo declarado
  cuando aplique, fecha UTC, clave de operación y huella del contenido normalizado.
  Unicidad empresa/clave de operación para repetición segura. Reutilizar clave con
  contenido distinto produce conflicto, nunca otro conteo.
- `stock_movements`: diferencia firmada de cantidad y valor, fecha y FK compuesta
  al conteo y su empresa/producto; origen verificable, sin texto libre que suplante
  una compra o venta. Un movimiento por conteo con diferencia; ninguno si es igual.
  Incorporar otros orígenes con relaciones tipadas en sus respectivos hitos.

Cada conteo almacena además las diferencias firmadas y CHECK exige que sean las
restas de los snapshots. La FK del movimiento incluye empresa/producto/conteo y
ambas diferencias, por lo que no puede vincular otra valoración al mismo origen.
La unicidad de conteo en movimientos impide duplicarlo; el CHECK no admite un
movimiento de cantidad cero. El esquema permite conservar conteos sin diferencia.
CHECK exige costo declarado en entradas sin valoración histórica utilizable, y
su ausencia cuando corresponde el histórico o no hay entrada.

El runtime no puede editar o borrar conteos/movimientos, ni borrar saldos. El saldo materializado
sí se actualiza exclusivamente desde el servicio autorizado. FK y CHECK no bastan
para probar igualdad entre libro y saldo: pruebas de transacciones y reconciliación
deben verificarla. No se promete aislamiento ante SQL arbitrario del rol runtime.

Downgrade bloquea escrituras e inspecciona las tres tablas; rechaza revertir si
cualquiera tiene datos, incluso un saldo cero. La reversión de esquema vacío
conserva el contrato previo. PostgreSQL NUMERIC puede redondear SQL directo; las
entradas de aplicación deben pasar por los validadores exactos existentes.

## Transacción implementada en 3.9

Una sola transacción: autorizar sesión admin y confirmación reciente; bloquear
empresa, usuario/sesión y producto en orden compatible con catálogo/accesos;
resolver clave de operación; leer/bloquear saldo; calcular desde ese saldo; insertar
conteo, movimiento si existe diferencia y actualizar saldo; revalidar autorización
antes de commit. El bloqueo de empresa serializa inicialmente sus escrituras y
protege también el primer saldo ausente; unicidad es la segunda barrera. No depender
solo de SELECT FOR UPDATE de una fila que todavía no existe.

Una repetición también exige autorización vigente, y devuelve la evidencia guardada
sin revalorar ni modificar existencias. Respuesta solo después de commit. Timeout
ambiguo se resuelve consultando/reintentando la misma clave con el mismo contenido,
nunca generando otra automáticamente. No mantener bloqueos durante llamadas a S3.

`InventoryAdministrator.record_count` recibe sesión, membresía esperada, entrada
tipada y UUID de operación; no recibe empresa. Revalida incluso las copias internas
de entrada. La huella SHA-256 incluye versión de operación, producto, cantidad,
motivo recortado y costo declarado. Decimales numéricamente iguales tienen la misma
representación; costo omitido y cero explícito son distintos. La clave de operación
no concede autoridad y no es una credencial.

Repetición autorizada devuelve snapshots y fecha originales, incluso si después
se desactivó el producto; no es una operación nueva. Clave con contenido diferente
produce conflicto. Nueva clave exige producto activo de la empresa. Una referencia
ausente, ajena o inactiva se deniega sin distinguir el caso. No hay reintentos
automáticos dentro del servicio. La evidencia del conteo conserva el actor original;
el contexto oculto del DTO corresponde al administrador que realiza la consulta.

La guardia administrativa se comparte con catálogo en
`access/administrator_guard.py`; mantiene bloqueo de empresa, resolución de sesión,
rol admin y confirmación reciente. El módulo preexistente `access/administration.py`
permanece intacto. La transacción usa los límites existentes de bloqueo/sentencia.

La reconciliación cantidad/valor del libro frente al saldo se comprueba en pruebas;
todavía no existe una herramienta operativa de reconciliación. 3.10 incorpora
saldo y consulta paginada de conteos por producto; la interfaz sigue pendiente.
Los errores de valoración (por ejemplo falta de costo) se traducen a
`InventoryInvalid` y JSON 422 genérico sin publicar detalles internos.

## Pruebas de persistencia requeridas

Empresa ajena, usuario revocado, perfil insuficiente, confirmación expirada incluso
antes de commit; producto inactivo; primer saldo concurrente; dos conteos simultáneos;
misma clave igual/distinta; fallo de inserción de evidencia/movimiento/saldo y de
commit; no-op con evidencia; conservación y reconciliación de cantidad/valor;
prohibición SQL de editar evidencia; migración y downgrade que rechace borrar datos.
Las pruebas de servicio ejecutan autorización, rollback y transacciones concurrentes
en PostgreSQL. 3.10 añade pruebas HTTP de origen, CSRF, cookies, límite de cuerpo,
contexto y errores seguros. El formulario necesitará validación propia antes de
habilitar operaciones de inventario desde la interfaz.
