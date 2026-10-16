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
