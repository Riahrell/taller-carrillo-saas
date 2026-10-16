# Modelo inicial y límites transaccionales

Este es el modelo objetivo. En acceso 2.1 están implementadas únicamente
`tenants`, `users` y `tenant_memberships`, mediante `0002_access`. Los atributos comerciales
se limitan a los requeridos por el plan y a las decisiones de `decisions.md`.
La especificación de cada tarea cerrará contratos antes de generar código.

## Entidades

| Grupo | Entidades y relaciones |
| --- | --- |
| Acceso | `users`, `tenants`, `tenant_memberships`, `sessions`; usuario global, membresía por empresa y sesión revocable. |
| Catálogo | `categories`, `products`, `workshop_services`; producto con SKU opcional único por empresa, precio y estado activo. |
| Inventario | `stock_movements`, `stock_balances`; movimiento con producto, cantidad, valoración, origen, autor y fecha; proyección única por tenant/producto. |
| Compras | `suppliers`, `purchases`, `purchase_items`; recepción completa enlazada a movimientos, una vez. Pago de compra separado de la recepción. |
| Clientes | `customers`, `vehicles`; orden conserva sus relaciones históricas aunque cambie el propietario actual. |
| Taller | `work_orders`, `work_order_items`, `work_order_status_history`, `work_order_attachments`; consumos vinculados al detalle de repuesto. |
| Ventas | `sales`, `sale_items`, `payments`; detalle conserva nombre, precio y costo aplicado. Origen directo o vinculado a orden. |
| Correcciones | Registros de anulación y compensación enlazados al original, sin borrado; diseño físico detallado en hito 5. |
| Caja | `cash_sessions`, `cash_movements`, `expenses`; pagos de compras y gastos con origen verificable, sin duplicar efectos. |
| Trazabilidad | `audit_events`, registro técnico de idempotencia con empresa, operación, clave, huella y resultado. |

No crear tablas de reportes por defecto: son consultas sobre fuentes identificadas.
No añadir entidades de crédito, nómina, lotes, almacenes o contabilidad formal.

## Restricciones

- IDs técnicos UUID. Timestamps con zona horaria en UTC.
- Todas las entidades de negocio incluyen tenant o una derivación inequívoca.
  Preferir tenant explícito y FK compuesta `(tenant_id, id)` en referencias
  entre entidades de negocio para impedir cruces incluso fuera del router.
- Membresía única `(tenant_id, user_id)`, SKU no vacío único por tenant cuando
  exista, saldo único `(tenant_id, product_id)`.
- Una sesión de caja abierta por tenant mediante restricción concurrentemente
  segura. Una venta activa por orden, conservando las ventas anuladas.
- Líneas de producto o servicio identifican exactamente una clase de referencia.
- CHECK para cantidades, importes y estados según su semántica; no aplicar
  indiscriminadamente importe positivo a compensaciones o diferencias.
- Sin cascadas que borren ventas, pagos, movimientos ni auditoría.
- Catálogo desactivable, relaciones históricas preservadas.
- Relaciones de origen de stock, pagos y caja deben ser verificables. No usar
  únicamente un texto libre como sustituto de integridad referencial.

## Unidades de trabajo

1. Recepción: compra validada y bloqueada -> movimientos de entrada -> saldo y
   valoración -> recepción confirmada -> auditoría e idempotencia -> commit.
2. Consumo: orden y autorización -> detalle -> saldo bloqueado -> salida y
   valoración -> vínculo al consumo -> auditoría e idempotencia -> commit.
3. Venta directa: autorización/caja -> precios y disponibilidad -> líneas y pagos
   -> salidas y caja -> auditoría e idempotencia -> commit.
4. Venta de orden: comprobar consumos previos -> venta y pagos -> caja ->
   auditoría e idempotencia -> commit; no repetir las salidas de repuestos.
5. Cierre: bloquear sesión contra cobros concurrentes -> calcular fuentes ->
   contado/diferencia/motivo -> cierre y auditoría -> commit.
6. Anulación: conservar original -> registrar reembolso y su estado real ->
   compensaciones autorizadas, con stock solo ante retorno físico -> auditoría.
   No mantener transacciones abiertas mientras se realiza un reembolso externo.

Cada implementación definirá su orden global de bloqueos y probará conflictos
entre cobro, consumo, cierre y anulación, no solo entre dos ventas idénticas.

## Fuentes para reportes

- Ventas: ventas confirmadas y compensaciones financieras, sin borrar originales.
- Cobros: pagos efectivos y reembolsos confirmados, separados por método.
- Efectivo: apertura y movimientos de caja; pagos no efectivos no lo alteran.
- Existencias y valoración: movimientos reconciliados con saldos materializados.
- Costos: salidas valoradas y retornos físicos compensatorios; no sumar de nuevo
  compras ni duplicar consumo de orden con su snapshot de costo en venta.
- Gastos: gastos registrados; movimiento de pago enlazado no es un segundo gasto.
- Compras: adquisición y pagos consultables separadamente.
- Cualquier indicador de utilidad requiere política explícita de reconocimiento,
  costos e impuestos antes de etiquetarlo como tal.

## Contrato implementado: acceso 2.1

- Los tres modelos tienen `id` UUID generado en servidor, `created_at` con zona
  horaria y valor UTC de DB, e `is_active` obligatorio con valor inicial true.
- `tenants`: `name` obligatorio (hasta 200 caracteres), no vacío.
- `users`: `email` global único (hasta 254), normalizado con strip/lower por el ORM
  y comprobado con lower/trim por DB; `password_hash` obligatorio (hasta 512), no
  vacío y sin valor de reserva. Acceso 2.2 incorpora validación del correo
  y generación/verificación Argon2id
  desde el bootstrap CLI. El CHECK de DB no verifica el formato criptográfico;
  los futuros casos de uso deben utilizar estas utilidades.
- `tenant_memberships`: FK restrictivas a tenant y usuario, `role` obligatorio
  sin valor por defecto, CHECK con admin/cashier/mechanic/read_only y pares únicos
  `(tenant_id, user_id)` y `(tenant_id, id)`. El segundo permite futuras FK
  compuestas hacia membresías. Se reutiliza la membresía al reactivarla.
- La pertenencia a otra empresa no concede acceso a la seleccionada. El resolvedor
  interno consulta los tres estados activos y vuelve a leer el rol almacenado.
  Su resultado no concede permisos y no debe reutilizarse entre peticiones.
- Esta fase no crea sesiones, configuración monetaria/zona del tenant, auditoría,
  permisos de módulos ni APIs. Las decisiones USD y America/El_Salvador siguen
  vigentes para cuando se implementen sus casos de uso.
- No hay RLS. El rol DB de aplicación tiene DML sobre estas tablas; el aislamiento
  probado corresponde al resolvedor interno, no a consultas SQL arbitrarias.
  La futura capa HTTP necesitará identidad y tenant desde sesión verificada.
- No se permiten borrados de empresas/usuarios referenciados por membresías.
  La protección del último administrador y la revocación de sesiones requieren
  servicios transaccionales posteriores; no están resueltas por estas FK.
