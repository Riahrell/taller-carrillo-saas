# Inventario privado — pantalla 3.11

`/inventario` comprueba sesión antes de montar información privada, solo admin.
Empresa y rol vienen de sesión; enlace desde el panel. `/catalogo` continúa como
administración y `/productos` público sigue pendiente. Crear producto no lo publica.

Ficha: nombre, SKU, categoría, estado, cantidad, costo promedio, precio de venta,
valoración interna e historial de conteos. Costo null significa «Sin costo
calculable»; cero explícito se distingue. Valoración/costo conservan hasta seis
decimales visibles y no representan cobros. Sin float; DTO estrictos y BigInt para
comprobar diferencias/promedios. Orden UTC conserva microsegundos; historial visible
en hora de El Salvador. Marcador accesible de imagen pendiente, sin foto inventada.

Productos e historial en páginas de 25. Categoría resuelta mediante páginas
autorizadas; carga/actualización cancelables, plazo global 30 segundos. Muchas
categorías pueden requerir una consulta de detalle más eficiente en otra entrega.
Son lecturas vivas separadas, sin prometer snapshot conjunto de ficha/historial.

## Conteo y resultado incierto

Identidad antes de montar campos, con contraseña/TOTP cuando corresponda; confirmación
nueva por recorrido, plazo conservador hasta 270 segundos sin renovarlo con lecturas.
El servidor conserva la autoridad y revalida antes del commit. Cantidad final
explícita, motivo y costo declarado solo para aumento sin valoración histórica
utilizable. La necesidad indicada por la UI es orientativa y puede cambiar en DB.
Revisión antes de enviar; no se mandan saldo previo, diferencias, precio ni empresa.

Clave `crypto.randomUUID` en HTTPS/localhost. Pendiente inmutable guardado antes del
POST, asociado a usuario/membresía/empresa. Bloqueo de doble clic y sin reintento
automático. Éxito exige respuesta coincidente, sesión actual y actualización de
consultas. No se compara el saldo actual con el snapshot del conteo porque puede
haber operaciones posteriores de otro administrador.

422 conocido: retira pendiente, actualiza saldo y vuelve a edición/revisión.
428: confirma identidad conservando clave/contenido. Red/503: «Resultado por confirmar»;
resolver exige identidad, revisión y clic explícito con exactamente la misma operación.
No asumir rollback si faltó respuesta. Cancelar el recorrido no deshace un commit.

Focus/pageshow/visibilidad/BroadcastChannel revalidan. Al ocultar, desmontan datos y
borradores, abortan/ignoran peticiones tardías. El pendiente sobrevive al remontaje
en memoria del contenedor y solo se presenta tras verificar el contexto original
admin; no se envía ni revela en otra empresa. Sin localStorage, sessionStorage,
IndexedDB, URL o cookie con el conteo. Cerrar/recargar/abandonar puede perder la copia
local: la UI avisa revisar historial antes de iniciar otra operación. No hay
recuperación automática del borrador después de cerrar el navegador.

Fotos Contabo, publicación pública, compras/consumos/ventas y vistas operativas de
otros perfiles siguen pendientes. La interfaz no sustituye permisos ni transacciones.

## Imagen del producto (3.15)

La ficha muestra la imagen privada actual. Cambiarla exige identidad reciente,
seleccionar cámara o galería, revisar y guardar explícitamente. Una respuesta
incierta conserva en memoria el archivo y UUID para reintento manual idéntico.
Cambio de empresa/sesión bloquea la operación y aborta la lectura. Tras recargar,
revisar la imagen actual antes de iniciar otra operación. Sin permisos S3 o sin
configuración, la ficha financiera sigue disponible con imagen no disponible.
