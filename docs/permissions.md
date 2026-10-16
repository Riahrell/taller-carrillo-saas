# Permisos iniciales

Permisos comprobados en backend y con tenant autorizado en todas las operaciones.
Lo no concedido se deniega. Los controles de interfaz solo acompañan esa decisión.

| Acción | Administrador | Cajero | Mecánico | Solo lectura |
| --- | --- | --- | --- | --- |
| Empresa, usuarios y roles | Sí | No | No | No |
| Catálogo: crear/cambiar precio/desactivar | Sí | No | No | No |
| Catálogo: consulta operativa activa | Sí | Sí | Solo repuestos/servicios necesarios, sin costos | Si se concede módulo |
| Compras, proveedores y ajustes | Sí | No | No | Consulta si se concede módulo |
| Consultar disponibilidad | Sí | Sí | Para órdenes asignadas | Si se concede inventario |
| Clientes/vehículos: alta y actualización | Sí | Sí | No | No |
| Clientes/vehículos: consulta | Sí | Sí | Solo datos necesarios en órdenes asignadas | Si se concede módulo |
| Crear orden y registrar recepción | Sí | Sí | No | No |
| Asignar técnico y aprobar reparación | Sí | No | No | No |
| Recepción -> diagnóstico | Sí | No | Solo orden asignada | No |
| Diagnóstico y notas | Sí | No | Solo orden asignada | No |
| Diagnóstico -> aprobación | Sí | No | Solo orden asignada | No |
| Aprobación -> reparación | Sí | No | No | No |
| Consumir repuestos en reparación | Sí | No | Solo orden asignada | No |
| Reparación -> listo | Sí | No | Solo orden asignada | No |
| Listo -> entrega, con cobro completo | Sí | Sí | No | No |
| Venta directa/cobro de orden | Sí | Sí | No | No |
| Abrir/cerrar caja | Sí | No | No | No |
| Consultar caja abierta | Sí | Sí | No | Si se concede caja |
| Gastos, pagos de compra, movimiento manual | Sí | No | No | No |
| Anular/cancelar/registrar compensación | Sí | No | No | No |
| Reportes financieros, costos e historial caja | Sí | No | No | Si se concede expresamente |
| Auditoría | Sí | No | No | No |

Solo lectura empieza sin módulos autorizados. El administrador concede consultas
por módulo; no hay acciones de escritura. Si se concede un módulo financiero se
registra esa autorización explícita, sin acceso indirecto desde otro módulo.

Los cambios de roles y accesos se auditan y revocan las sesiones afectadas.
La administración de precios no habilita enviar precios arbitrarios al POS:
el cambio sucede en catálogo con permiso propio, validación y auditoría.

## Implementado en acceso 2.9

Solo la acción interna de cambiar rol/estado de una membresía está autorizada:
exige token de sesión válido y rol admin actual en su empresa. No recibe tenant,
actor ni contexto elegido por el llamador. Los valores role/is_active describen
el cambio solicitado, validado por el servicio; no acreditan privilegios.
Cajero, mecánico y solo lectura no pueden elevarse ni cambiar otros accesos.

El objetivo se consulta dentro de la empresa de la sesión, incluidos accesos
inactivos para poder reactivarlos. No altera usuario global ni otras membresías.
Un cambio efectivo revoca todas las sesiones de esa membresía y los pendientes
globales del usuario afectado; se registra el antes/después y autor en el mismo
commit. Reactivar no restaura tokens revocados. Un estado idéntico no genera evento
ni revoca tokens, aunque la comprobación válida renueva actividad de la sesión.

No se puede desactivar ni degradar al último administrador con usuario y membresía
activos. Si otro administrador tiene usuario o membresía inactivos, no cuenta.
El propio administrador puede retirarse solo si queda otro; su sesión se revoca.
Esta garantía corresponde al servicio coordinado, no a SQL arbitrario ni a futuras
operaciones globales de usuario aún no implementadas. No existe API/pantalla para
administrar estos cambios ni concesiones por módulo para solo lectura todavía.
