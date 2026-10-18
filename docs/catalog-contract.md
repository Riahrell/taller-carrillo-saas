# Catálogo interno — contrato inicial

Fuente: alcance de catálogo del Plan Maestro v1.1, modelo y decisiones existentes.
Se implementa por partes: 3.2 incorpora persistencia, sin API/UI ni permisos de
lectura/escritura comerciales habilitados todavía.

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

## Próxima conexión de casos de uso

El servicio obtendrá empresa/actor/rol de la sesión vigente, volverá a autorizar
dentro de la transacción y aplicará los permisos de `permissions.md`. Crear o
cambiar precio será administrativo y auditado en el mismo commit. Aún no se
amplió la auditoría tipada para estas acciones; no habilitar escrituras sin ella.
Las entradas no admitirán tenant, autor, rol, stock ni costo calculado del cliente.
El precio solicitado en una edición administrativa no será autoridad de precio
en venta: el cobro consultará el catálogo autorizado en servidor.

No existe RLS ni aislamiento contra SQL arbitrario del rol runtime. La FK protege
la relación, no sustituye filtro por empresa y autorización de cada consulta.
Probar IDs ajenos, sesión revocada, perfiles no autorizados, rollback de auditoría,
conflictos de edición y colisiones de SKU antes de exponer formularios.

Servicios de taller, publicación y movimientos se incorporarán por separado.
Crear, editar o desactivar un producto no crea ni altera existencias.
