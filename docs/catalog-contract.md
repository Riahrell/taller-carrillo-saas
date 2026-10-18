# Catálogo interno — contrato inicial

Fuente: alcance de catálogo del Plan Maestro v1.1, modelo y decisiones existentes.
Se implementa por partes: 3.2 incorpora persistencia y 3.3 servicios internos de
alta con autorización y auditoría. Todavía sin API/UI de catálogo habilitadas.

## Persistencia 3.2

- `categories`: UUID servidor, empresa, nombre, estado activo y creación UTC.
- `products`: los mismos atributos, categoría opcional, SKU opcional y precio
  NUMERIC(20,2) obligatorio, sin precio predeterminado. Sin stock, costo,
  impuestos, descuentos, imágenes ni estado de publicación en esta tabla.
- Límites técnicos adoptados: nombre de hasta 200 caracteres y SKU de hasta
  100. Nombre no vacío; SKU no vacío y sin espacios exteriores cuando exista.
  El SKU se compara exactamente, sin inventar normalización de mayúsculas.
  No imponer unicidad del nombre de producto o categoría.
- La categoría opcional permite registrar un producto aún sin clasificar; no
  implica que un producto interno pueda publicarse sin las reglas del hito público.
- SKU único por empresa, incluyendo productos desactivados. NULL admite varios
  productos sin SKU; la misma referencia se permite en empresas diferentes.
- FK compuesta empresa/categoría impide asociar categorías ajenas desde SQL.
  Claves únicas empresa/id preparan futuras referencias compuestas de stock.
- Precio no negativo y finito. No presume una política de impuestos ni autoriza
  cobros; los futuros contratos rechazarán exceso de precisión antes de PostgreSQL,
  cuyo tipo NUMERIC puede redondear entradas. No usar floats en esos contratos.
- Estado inicial activo, fechas de DB con zona; no hay backfill ni productos demo.
- Runtime sin DELETE de catálogo; las relaciones usan RESTRICT. Downgrade 0030
  se niega si cualquiera de las dos tablas tiene datos, incluso inactivos.

## Servicios internos 3.3

`CatalogAdministrator` obtiene empresa/actor/rol de la sesión vigente y exige
administrador con confirmación reciente, más comparación con la membresía
esperada. La comparación no elige actor o empresa. Bloquea empresa -> usuario ->
sesión -> categoría, compatible con administración de accesos. Vuelve a comprobar
la autorización y su ventana antes de commit; no extiende el plazo de confirmación.

`CategoryCreation` recibe nombre. `ProductCreation` recibe nombre, precio exacto,
SKU y categoría opcionales. Precio JSON fraccionario en string, no float; rechaza
no finitos, negativos, exceso de tamaño o más de dos decimales sin redondear.
Nombre/SKU se recortan en los extremos, mantienen mayúsculas, rechazan NUL y
Unicode inválido. Campos extra se rechazan, incluyendo tenant, autor, rol, stock,
costo y estado. El precio cero es explícito, nunca valor predeterminado.

Una categoría indicada debe estar activa y pertenecer a la empresa de la sesión;
referencia ausente/ajena/inactiva produce el mismo rechazo. SKU duplicado dentro
de empresa produce conflicto; no se vincula ni modifica el producto existente.
El servicio devuelve únicamente después de commit, sin empresa, actor ni secretos
en los DTO. Fallo de DB/commit no devuelve resultado ni provoca reintento automático.
Todavía sin idempotencia general de altas: el futuro adaptador no debe repetir una
solicitud cuya respuesta se haya perdido.

`0031_catalog_audit` amplía `audit_events` con acciones
`catalog_category_created` y `catalog_product_created`. Conserva los CHECK de
membresías y exige formas separadas: objetivo de membresía para sus eventos;
producto/categoría de la misma empresa y snapshot inicial de nombre/SKU/precio
para catálogo. No hay payload libre. Autor y evidencia se guardan con el alta en
un commit. UPDATE/DELETE siguen prohibidos al runtime. Downgrade se niega si hay
eventos nuevos, preservando evidencia. No se reescriben eventos históricos.

Consulta, edición/desactivación, paginación y transporte siguen pendientes. El
precio solicitado en una futura edición administrativa no será autoridad de precio
en venta: el cobro consultará el catálogo autorizado en servidor.

No existe RLS ni aislamiento contra SQL arbitrario del rol runtime. La FK protege
la relación, no sustituye filtro por empresa y autorización de cada consulta.
Mantener las pruebas de IDs ajenos, sesión revocada, perfiles no autorizados,
rollback de auditoría y colisiones de SKU; agregar conflictos de edición antes
de exponer esos formularios. HTTP deberá exigir CSRF/origen y vincular contexto.

Servicios de taller, publicación y movimientos se incorporarán por separado.
Crear, editar o desactivar un producto no crea ni altera existencias.
