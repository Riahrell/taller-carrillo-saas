# Plan incremental y aceptación

Estado: hito 0 documentado y fundación 1.3 ejecutada. Backend, PostgreSQL,
migraciones, frontend inicial y proxy del mismo origen con pruebas locales.
CI sigue pendiente. Resultados actuales en `deliveries/foundation-1.3.md`;
las entregas anteriores quedan como registros históricos.
No confundir criterios futuros de esta lista con verificaciones realizadas.

## Hitos

| Hito | Entregable | Puerta de aceptación |
| --- | --- | --- |
| 0 Diseño | Decisiones, modelo, permisos y criterios | Decisiones críticas documentadas; política fiscal identificada como bloqueo para cobro real. |
| 1 Fundación | Backend/frontend mínimos, PostgreSQL, Alembic, Compose, CI y documentación | Arranque limpio reproducible; migración en PostgreSQL vacío; lint, tests base y build ejecutados. |
| 2 Acceso | Usuarios, membresías, sesión, CSRF y bootstrap | Aislamiento y permisos comprobados; usuario/membresía inactivos y sesión revocada rechazados. |
| 3 Inventario | Catálogo, compras, recepción y stock | Concurrencia, idempotencia, costeo y reconciliación comprobados con PostgreSQL real aislado. |
| 4 Taller | Clientes, vehículos y órdenes | Estados, asignación, consumo único y preservación histórica comprobados. |
| 5 Cobro | Ventas, pagos, caja, gastos, comprobante y anulación total | Atomicidad, idempotencia, cierre concurrente y compensaciones comprobados; política fiscal validada antes de uso real. |
| 6 Reportes | Ventas, costos, gastos, stock, resúmenes | Fuentes reconciliadas; fechas locales, límites y días vacíos correctos. |
| 7 Producción | HTTPS, hardening, backups y runbook | Despliegue y recuperación ensayados; copia de DB y adjuntos restaurada; riesgos operativos resueltos. |

## Casos imprescindibles

- Dos tenants con recursos diferentes: denegar lectura/escritura de IDs ajenos
  en todos los módulos, incluyendo adjuntos, reportes y relaciones anidadas.
- Manipular tenant, rol, precio, costo, stock o total en una petición de venta
  no cambia la autoridad del servidor. Campos extra de contratos de escritura
  se rechazan cuando no pertenecen al caso de uso.
- Login con error genérico; usuario/membresía inactivos; expiración absoluta e
  inactiva; logout; revocación; CSRF y límites de intentos compartidos.
- Cambio de empresa no conserva carrito ni presenta caché de la empresa previa.
- Bootstrap sin admin público ni contraseña de reserva; rollback completo si falla.
- SKU único por empresa; misma referencia permitida en empresas distintas.
- Cantidades cero/negativas o con más precisión que la admitida se rechazan.
- Entrada, salida, conteo físico, diferencia cero, ajuste positivo sin costo,
  stock insuficiente y primera creación concurrente de saldo.
- Dos conexiones intentan retirar la última unidad: una operación confirma,
  otra falla y el libro coincide con el saldo, sin cantidades negativas.
- Recepción duplicada y operaciones multiproducto con orden de bloqueos estable.
- Promedio ponderado, salidas a costo histórico, agotamiento sin residuos y
  reconciliación de cantidad/valoración después de compensaciones.
- Orden con vehículo o técnico ajeno; transición inválida; mecánico no asignado;
  entrega sin cobro; consumo repetido; cambio posterior de propietario.
- Cobrar una orden no repite consumos; venta directa sí descuenta sus productos.
- Cambio de precio entre revisión y confirmación exige nueva revisión del total.
- Pagos combinados completos, insuficientes y excesivos; solo efectivo altera
  efectivo esperado; efectivo recibido/cambio no infla lo aplicado.
- Fallo inyectado entre venta, pago, stock y caja revierte todo, incluido el
  resultado de idempotencia. Misma clave/contenido devuelve mismo resultado;
  diferente contenido falla; reintentos simultáneos no duplican operaciones.
- Cobro simultáneo con cierre: se confirma íntegramente antes del cierre o se
  rechaza, sin movimientos incorporados después a una sesión ya cerrada.
- Anulación duplicada, reembolso externo pendiente/confirmado, egreso en caja
  actual con caja original cerrada, retorno físico y ausencia de retorno.
- Un gasto o pago de compra genera su efecto de caja una vez y no duplica
  costo de inventario. Servicio no genera movimiento de existencias.
- Reportes [inicio, fin), zona local, día sin ventas en cero, costos sin doble
  conteo y totales rastreables hasta operaciones originales/compensatorias.
- Frontend: formularios, carrito, errores HTTP, estados vacíos/carga y foco,
  teclado y diálogos. Los precios mostrados nunca sustituyen al servidor.
- E2E: compra/recepción -> orden/consumo -> cobro -> entrega -> verificar stock,
  caja y reportes; agregar recorrido de corrección y anulación.
- Adjuntos: tamaño/tipo/firma/dimensiones inválidos, acceso entre empresas,
  archivo huérfano y restauración conjunta con metadata.

## Contrato de cada tarea

1. Una tarea pequeña con archivos completos en el repositorio.
2. Comandos exactos de ejecución y validación realmente utilizados.
3. Resultados reales y limitaciones; no afirmar que pasó un comando no ejecutado.
4. Archivos cambiados, riesgos abiertos y siguiente tarea concreta.
5. Pruebas pertinentes a la tarea; integración en base aislada, nunca producción.
6. Cada hito termina con arranque, pruebas y builds correspondientes comprobados.
7. Rama por funcionalidad y commits pequeños cuando se realicen commits; no
   mezclar cambios ajenos ni dejar artefactos temporales/versionados por accidente.

El README contiene los comandos de instalación, arranque y validación de la
fundación actual. Los comandos de módulos futuros se incorporarán con su tarea.
