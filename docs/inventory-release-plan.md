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
| Productos | Crear, listar, detalle y editar nombre, categoría, SKU, precio y estado con control de versión y auditoría | Validación manual del propietario |
| Buscador | Nombre o SKU parcial y categoría combinables; filtro de empresa y paginación en servidor | Validación manual del propietario |
| Fotos | Carga privada S3, cámara/galería y lectura | Validar miniatura al volver al catálogo y cámara en móvil físico |
| Existencias | Saldo, costo promedio, conteos, entradas/salidas manuales y reversa controlada del último movimiento manual/ajuste | Validación manual; anulaciones de compras/ventas dependen de sus documentos |
| Recepción de compra | API y transacción de recepción | Pantalla de proveedores/compra/recepción |
| Salida por venta u orden | No integrada | Necesita origen real y su módulo; no basta un campo de texto que diga venta |
| Historial | Historial unificado de ajustes, entradas/salidas manuales y recepciones, con origen, responsable y paginación | Validación manual del propietario; ventas/órdenes dependen de sus módulos |
| Stock bajo | Mínimo configurable, aviso en ficha y lista global privada de productos por reponer | Validación manual; consulta a demanda |
| Acceso | Email de seis caracteres, navegador 15 días y sesión opaca; regresión de CI cerrada en 3.28 | Registro/aprobación, consulta autorizada y salidas manuales de Cajero implementados; aceptación manual pendiente |
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

El propietario conserva las pruebas manuales. Los resultados técnicos ejecutados
se documentan por entrega; la 3.39 incluye concurrencia, rollback y restauración
local con conciliación del inventario. La aceptación manual y la operación remota
siguen siendo condiciones pendientes del cierre para producción.


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

Entrega 3.31 agrega el selector de categoría al buscador de Inventario y Catálogo.
Todas las categorías elimina ese filtro; Limpiar búsqueda elimina texto y categoría.

Entrega 3.35 unifica el historial privado por producto, incluyendo ajustes sin
cambio y recepciones reales existentes. Muestra responsable, fecha y referencia
del origen, con paginación conjunta. No agrega ventas, órdenes ni reversas.
También conserva la lista del catálogo durante consultas y formularios para
reducir saltos de desplazamiento. La siguiente tarea es edición y estado del
producto; continúan pendientes stock bajo, reversas, registro/permisos y cierre
operativo del despliegue. La pantalla de compras/recepciones sigue pendiente.

Entrega 3.36 agrega edición y activación/desactivación de productos desde Catálogo,
con revisión previa, control de versión y evidencia antes/después. Conserva stock,
costo, fotos e historial y no expone escrituras de inventario en este formulario.
La siguiente tarea es configurar stock bajo por producto, sin umbrales inventados.

Entrega 3.37 configura mínimos explícitos y aviso en la ficha del producto, con
versión y evidencia append-only. No cambia cantidades ni costos. Los productos
existentes siguen sin mínimo hasta una acción explícita. El listado global por
reponer queda para la siguiente tarea, antes de reversas y cierre de permisos.

Entrega 3.38 agrega la vista global de productos por reponer, con foto, cantidad,
mínimo vigente, acceso a la ficha y paginación. La consulta respeta empresa,
estado y último mínimo. Sigue la reversa controlada de movimientos manuales y
ajustes; recepciones de compra y ventas requieren sus propios flujos documentales.

Entrega 3.39 agrega la compensación idempotente del último movimiento manual o
ajuste con diferencia, sin operaciones posteriores y con saldo intacto. Invierte
cantidad y valoración originales, conserva historial y rechaza repeticiones.
No cancela recepciones de compra, ventas u órdenes. Continúan pendientes registro
y permisos de empleados, revisión manual y cierre operativo del despliegue.

Entrega 3.40 incorpora únicamente la preparación/listado/revocación de invitaciones
privadas de empleados en backend. No crea cuentas, membresías o permisos y aún
no tiene pantalla ni consumo público. El registro y su aprobación continúan como
siguiente bloque, sin cambiar la autorización administrativa del inventario.

Entrega 3.41 agrega registro por invitación, contraseña del empleado, verificación
de correo y aprobación administrativa desde UI. Aprobar crea read_only sin módulos.
Continúan pendientes permisos de inventario para empleados, cuentas existentes,
retención de intentos y recuperación antes del primer login, además de aceptación
manual y operación remota. No hay cambios de stock ni despliegue en Contabo.

Entrega 3.43 permite conceder/revocar consulta privada a Solo lectura, con auditoría
y versión. La pantalla operativa excluye costos/valoración y usa fotos reautorizadas.
Desactivar/reperfilar retira la concesión; reactivar no la restaura. Sigue la
autorización de escrituras de empleados, además de los cierres operativos y de
registro ya enumerados. No se despliega en Contabo ni cambia inventario.

Entrega 3.45 añade concesión explícita para Cajero: consulta y salidas manuales
con valoración de servidor e idempotencia, sin costos en el DTO. Solo lectura no
escribe y Mecánico no recibe operaciones fuera de órdenes asignadas. Entradas,
ajustes y anulaciones de empleados no se abren en esta entrega. Sigue el cierre
operativo y de aceptación manual para VPS.

Entrega 3.48 optimiza fotos nuevas/reemplazadas a WebP con miniatura privada de
300 px para listas; detalle hasta 1600 px y compatibilidad PNG anterior. La
publicación de ambas variantes y su limpieza mantienen reserva/evidencia y
reintentos. El catálogo conserva páginas de 25 productos, máximo API 100 por
solicitud. Sin conversión masiva, CDN pública o ampliación de permisos.


Entrega 3.49 conserva la pantalla y las miniaturas al recuperar el foco mientras
se revalida el acceso. El propietario confirmó que dejó de aparecer el blanco
tras recargar la versión publicada localmente.

Entrega 3.50 amplía el ensayo aislado de restauración a los metadatos de fotos
WebP y miniaturas: enlace vigente del producto, claves, tamaños, dimensiones,
hashes y evidencia del responsable. No respalda ni restaura los objetos de Contabo;
esa recuperación, la aceptación manual y el entorno HTTPS siguen pendientes.

Entrega 3.51 añade exportación privada de fotos vigentes/miniaturas, verificación
offline y restauración condicional sin sobrescritura. PostgreSQL temporal y
transporte simulado validan el recorrido; no se ejecuta contra Contabo. Sigue
pendiente ensayar un conjunto coordinado de dump y objetos con destino privado
independiente, además de HTTPS y aceptación manual para el despliegue.

Entrega 3.52 añade check-database para exigir correspondencia exacta entre el
archivo privado de fotos/miniaturas y las referencias de la base recuperada, sin
escrituras ni acceso S3. El propietario confirmó que aún no tiene segundo bucket;
el ensayo real sigue pendiente de ese destino separado. La validación aislada
seleccionada pasó 16 pruebas. No se cambia Contabo ni la VPS.

Entrega 3.53 completa el ensayo real en el bucket privado separado
`taller-carrillo-restauracion`, con PostgreSQL temporal e imágenes sintéticas.
Se validan pg_dump/pg_restore, pérdida y recuperación de objetos, correspondencia,
repetición, denegación anónima y limpieza. No se accede al bucket de imágenes
reales ni a la VPS. Sigue preparar despliegue HTTPS revisable y respaldar el
sistema remoto anterior antes de sustituirlo, además de aceptación manual.

Entrega 3.54 retira las aplicaciones antiguas de la VPS por petición expresa del
propietario, quien confirmó que eran datos de prueba sin necesidad de respaldo.
Verificado: cero contenedores, volúmenes, imágenes y caché; directorios retirados;
SSH y Docker conservados. No se toca Object Storage ni el proyecto local. Ya no
se requiere conservar ese despliegue anterior; sigue la instalación HTTPS nueva,
la aceptación manual y el respaldo operativo de la aplicación nueva.
