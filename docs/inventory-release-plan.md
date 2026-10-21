# Cierre de Inventario e Inicio de sesión para despliegue

Objetivo del propietario: una experiencia sencilla de productos, fotos, entradas,
salidas y ajustes, con login listo antes de desplegar en Contabo.
Decisión confirmada: salidas manuales con motivo y salidas ligadas a ventas u
órdenes. No confundir un ajuste de saldo real con una entrada/salida de unidades.

Fuente revisada: Plan Maestro v1.1, sección 2 (MVP), sección 7
(contrato inventario), sección 15 (pruebas). Los documentos adjuntos son
referencia de requisitos; no autorizan accesos externos ni despliegues por sí solos.

## Estado real

| Parte | Implementación actual | Brecha antes de considerarla lista |
| --- | --- | --- |
| Productos | Crear, listar, detalle, categorías, SKU y precio | Edición/activación todavía sin flujo HTTP/UI completo |
| Buscador | Nombre o SKU parcial, sin distinguir mayúsculas; filtro de empresa y paginación en servidor | Validación manual del propietario |
| Fotos | Carga privada S3, cámara/galería y lectura | Validar miniatura al volver al catálogo y cámara en móvil físico |
| Existencias | Saldo, costo promedio, conteos y entradas/salidas manuales con cantidad positiva | Validación manual del propietario; reversas aún pendientes |
| Recepción de compra | API y transacción de recepción | Pantalla de proveedores/compra/recepción |
| Salida por venta u orden | No integrada | Necesita origen real y su módulo; no basta un campo de texto que diga venta |
| Historial | Conteos y entradas/salidas manuales en secciones separadas | Historial unificado con recepciones, origen y responsable visible |
| Stock bajo | No configurado | Definir y configurar umbral por producto, sin inventar valores |
| Acceso | Email de seis caracteres, navegador 15 días y sesión opaca; regresión de CI cerrada en 3.28 | Registro de empleados y aprobación/permisos pendientes |
| Operación | Compose local, configuración externa de S3/correo | Preparar entorno de pruebas separado, HTTPS, backup/restauración y despliegue revisable |

No se declara el conjunto listo para producción. La revisión estática y una
compilación correcta no validan concurrencia, aislamiento ni una restauración.

## Entregas pequeñas en orden

1. Miniaturas: renovar al regresar, invalidar después de guardar una foto,
   distinguir carga/ausencia/error y permitir reintento. Validación manual.
2. Movimientos manuales: servicio transaccional común y evidencia tipada IN/OUT.
   Cantidad positiva como delta; IN recibe costo explícito; OUT usa el costo
   del saldo bloqueado y rechaza insuficiencia. Repetición con misma clave no
   duplica; contenido distinto con esa clave se rechaza. Motivo obligatorio,
   usuario/empresa/rol del servidor. No crea venta, pago ni gasto.
3. UI: «Agregar producto», «Registrar entrada», «Registrar salida»,
   «Corregir existencias» y «Cambiar foto». Mostrar antes/después y confirmar.
   Un ajuste registra el saldo contado; una entrada/salida agrega/resta unidades.
   No calcular en el cliente un saldo final y enviarlo como si fuera un delta.
4. Recepciones de compra e historial unificado. No duplicar entrada al repetir
   la recepción. Separar costo de inventario y precio de venta.
5. Orígenes de venta/orden: integración tipada con documentos reales,
   autorización, estados y reversas auditables. Depende de esos módulos; no se
   declarará implementada creando referencias libres o usando ajustes manuales.
   Impuestos y medios de pago no se inventan para cerrar este punto.
6. Edición/estado del catálogo y configuración de stock bajo, con sus contratos.
7. Cierre de acceso, pruebas técnicas de invariantes y validación manual del
   propietario; auditoría de dependencias y secretos.
8. Preparación del despliegue de pruebas: configuración de panel.taller-carrillo.com,
   secretos externos, HTTPS, red privada, correo/S3, respaldo y restauración
   ensayada, salud y rollback. Revisar el resultado antes de tocar la VPS.

## Condiciones de aceptación del conjunto

- Producto/foto/costo/precio visibles con etiquetas claras y acceso solo propio.
- Entrada/salida/ajuste con origen, motivo, actor y fecha; libro y saldo conciliados.
- Salida insuficiente no modifica cantidad ni valor. Dos peticiones concurrentes
  por la última unidad no pueden consumirla ambas.
- Fallo intermedio revierte toda la operación y un reintento no duplica efectos.
- Reversas preservan historial. Ventas/órdenes no se simulan mediante conteos.
- Cambiar tenant/rol en el cliente no concede acceso ni autoridad sobre stock.
- Login y permiso de quince días mantienen caducidad, límites y revocación.
  Cierre de navegador usa cookie de sesión, sujeto a restauración del navegador;
  Cerrar sesión revoca explícitamente.
- No se inicia despliegue productivo con estas brechas sin resolver.

El propietario conserva las pruebas manuales. No se ejecutaron baterías
funcionales en esta revisión. Las pruebas de transacciones y seguridad siguen
siendo una condición pendiente del cierre para producción.


## Primer despliegue acotado a inventario

El propietario solicita terminar inventario e inicio de sesión para probar esa
parte en Contabo. El primer despliegue se preparará con esas funciones activas;
ventas, órdenes y cobros no se presentan como disponibles. Sus salidas vinculadas
siguen en el itinerario y se integrarán cuando existan esos documentos reales.
Los movimientos manuales no registran ventas, pagos ni compras financieras.

Entrega 3.29 incorpora servicio/API y UI de movimientos manuales, costo explícito
en entradas, costo del servidor en salidas, rechazo de insuficiencia e historial
manual paginado. Continúan pendientes el historial unificado, las reversas, la
edición/estado y stock bajo, el registro/permisos, la comprobación móvil y el
respaldo/restauración y HTTPS del entorno de pruebas. Esta entrega por sí sola
no declara el inventario completo ni autoriza cambios destructivos en la VPS.

Entrega 3.30 incorpora el buscador privado de productos en Inventario y Catálogo.
No reemplaza el trabajo pendiente de historial unificado, edición y stock bajo.
