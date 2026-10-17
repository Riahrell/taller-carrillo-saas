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
operaciones globales de usuario aún no implementadas. La entrega 2.9 dejó el
servicio interno; su conexión HTTP se detalla abajo. No hay concesiones por
módulo para solo lectura todavía.

## Conexión HTTP en acceso 2.10

GET/PATCH `/api/auth/memberships/{membership_id}` aplican ahora el permiso admin
en el servicio transaccional, para la empresa de la sesión. El contrato está en
`membership-http-contract.md`. No hay permisos de consulta de membresías para
otros perfiles, ni siquiera del propio acceso desde esa ruta administrativa.
La consulta de sesión existente sigue disponible para todos los perfiles activos.

Un administrador tampoco puede consultar/modificar membresías ajenas: mismo 403
que un objetivo inexistente, sin ETag ni datos. Sesión inválida devuelve 401.
No se expone correo, cuenta global, hash ni token. Un selector provisional no
autoriza administración. Origen/CSRF se comprueban para PATCH y la cabecera de
intención de navegador para GET, con CORS cerrado y respuestas no-store.

If-Match identifica el estado revisado, sin otorgar autoridad. Se comprueba tras
sesión/permiso bajo los locks del cambio: un estado diferente devuelve 412 sin
efectos. Si el cambio revoca la sesión del actor, las cookies se retiran después
de commit. La entrega 2.11 añade el listado descrito abajo; los permisos de los
módulos futuros siguen pendientes.

## Listado de membresías en acceso 2.11

GET `/api/auth/memberships` comprueba sesión y permiso admin actual antes de leer
cada página. La única empresa admitida sale de la sesión. limit/after controlan
solo tamaño/frontera; nunca tenant, rol ni autoridad. Un UUID ajeno/desconocido
como cursor no se consulta ni revela si existe; el filtro sigue siendo la empresa.

El administrador puede identificar las membresías mediante users.email existente
y consultar role, is_active y users.is_active como user_is_active. No se expone ID
global, hash, token ni otras empresas de la cuenta. Se incluyen accesos inactivos
sin habilitarlos; el cambio de estado de la cuenta global sigue fuera del alcance.
Otros perfiles, identidad pendiente o sesión inválida no reciben items/correos.
Las páginas no emiten cookies ni ETag, llevan no-store y salen tras commit.

## Pantalla de membresías en acceso 2.12

El enlace administrativo aparece solo con perfil admin verificado. Entrar por
URL directa vuelve a consultar sesión; otros perfiles no montan el listado. Esto
mejora la interfaz, pero la autoridad sigue en el servicio de cada GET/PATCH.
El rol elegido en el formulario es el estado solicitado del objetivo, nunca
el permiso del actor. No admite tenant, identidad ni cuenta global como entradas.

Las cabeceras X-Access-Membership y X-Expected-Membership vinculan respuesta y
confirmación al contexto ya autenticado; no conceden permisos ni eligen sesión.
La pantalla renueva CSRF, relee detalle y exige su If-Match. Ante conflicto no
sobrescribe, y ante respuesta perdida no repite PATCH. Se retiran datos al perder
acceso o cambiar visibilidad; señales entre pestañas solo solicitan otra lectura.
No hay garantía de actualización instantánea frente a cambios remotos mientras
una pestaña permanece sin interacción. Los controles DB siguen aplicándose en
la siguiente petición. Sin altas, permisos comerciales o auditoría consultable.
