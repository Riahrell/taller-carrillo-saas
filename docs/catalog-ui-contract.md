# Catálogo administrativo — interfaz 3.5

Ruta `/catalogo`, accesible desde la sesión de administrador. Comprueba la sesión
al montar, antes de mostrar registros; otros perfiles no consultan el catálogo.
La autorización real sigue en cada petición del servidor, no en el enlace.

- Listas independientes de productos/categorías: una página de 25 por clase,
  principio y siguiente página. Incluyen el estado activo para gestión admin.
- Alta con revisión explícita antes del POST. Nombre, SKU/categoría opcionales
  y precio obligatorio para producto; categoría nueva solo recibe nombre.
- Confirma identidad con el componente existente de contraseña/TOTP antes de
  montar campos de la primera alta. Reutiliza la prueba dentro de esta pantalla
  durante la ventana conservadora de hasta 270 segundos desde la confirmación;
  crear/cancelar/revisar otra alta no prolonga ese plazo. Servidor conserva su
  propia ventana y vuelve a comprobarla dentro de cada transacción.
- Caducidad retira un borrador abierto; si hay escritura en curso se informa
  resultado incierto. Si solo se consulta, caducar la confirmación no bloquea
  lecturas: la siguiente alta solicita otra prueba.
- Precios como texto decimal de extremo a extremo, sin Number/parseFloat.
  Admite coma decimal en el campo y envía string canónico con dos decimales.
  Rechaza mayor precisión/tamaño o separadores de miles; no redondea. Permite
  cero explícito sin preseleccionar precio. Presentación monetaria por strings.
- Selector de categorías activas paginado; conserva como máximo una selección
  adicional cuando cambia su página, sin cargar todas las categorías.
- POST prepara CSRF, envía la membresía esperada y solo campos conocidos.
  Los DTO y la cabecera de respuesta deben corresponder a esa membresía;
  campos adicionales, orden/cursor inválido o precio numérico se rechazan.
- Tras alta revalida usuario/empresa/membresía/rol. Para producto relee detalle
  y compara nombre, precio exacto, SKU y categoría; renueva páginas antes de
  mostrar éxito. Si no puede verificarlo, no afirma que la operación falló ni
  repite POST automáticamente: exige revisar acceso y catálogo.
- Conflicto de SKU o datos inválidos permite revisar borrador. 428 retira el
  borrador; errores inciertos bloquean la vista hasta comprobar acceso.
- Foco/pageshow/visibilidad y avisos entre pestañas invalidan el contexto local.
  Retira registros/borradores al ocultar, comprueba acceso al volver y descarta
  respuestas tardías de operaciones canceladas. Callbacks de una instancia
  desmontada no cambian el indicador de escritura de otra instancia.
- Datos solo en memoria: sin localStorage/sessionStorage, query con datos de
  formularios, caché persistente ni envío a terceros. React representa nombres
  como texto. No hay garantías de actualización remota instantánea mientras una
  pantalla permanezca abierta sin interacción; el backend revalida cada petición.

No hay edición/desactivación, búsqueda, publicación, costo, stock ni movimientos
en esta pantalla. Crear un producto no registra existencias. Contrato HTTP:
`catalog-http-contract.md`. No confundir esta entrega con inventario completo.
