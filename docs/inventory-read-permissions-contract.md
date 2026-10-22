# Consulta de inventario por concesión explícita — 3.43

Primera concesión operativa a empleados: consultar productos activos, fotos,
SKU/categoría, precio de venta y existencias. El perfil Solo lectura empieza sin
permiso; el administrador de su empresa lo concede y retira explícitamente.
No habilita costos, valoración, historial financiero, movimientos, catálogo
administrativo, cambios de precio/foto, mínimos ni administración de personal.
No se utiliza el perfil administrador para abrir inventario a un empleado.

## Concesión administrativa

GET/PATCH `/auth/memberships/{membership_id}/inventory-read` resuelve empresa y
actor desde la sesión. UUID de destino identifica solo la membresía revisada;
una ajena o inexistente devuelve el mismo 403. No acepta tenant, rol, actor,
revision ni permiso calculado desde el cliente.

GET exige admin actual, intención de navegador y X-Expected-Membership. Devuelve
membership_id, granted, eligible, effective y revision (cadena decimal), con
X-Access-Membership y ETag `"inventory-read-v1-UUID-revision"`. `effective` describe
esta concesión, no las facultades inherentes del administrador.

PATCH acepta únicamente `{ "granted": true/false }`, con origen/CSRF, admin actual,
confirmación de identidad, contexto esperado e If-Match de la versión revisada.
Sin If-Match: 428; inválido: 400; versión distinta: 412. No se sobrescribe una
revisión competidora. La UI exige revisión/confirmación y no reintenta escrituras
tras respuesta perdida. Un 412 descarta la propuesta y exige volver a consultar.

La concesión nueva solo puede darse a un usuario y membresía activos con perfil
read_only. Puede retirarse una concesión anterior aunque el acceso esté inactivo
o haya cambiado de perfil. Otros perfiles no heredan esta concesión. Los
administradores conservan su autoridad previa, sin necesitar una fila de permiso.

Sin filas, granted=false y revision=0. Cada cambio efectivo agrega una fila de
InventoryReadGrant con valor anterior/nuevo, autor, destino, fecha DB y revisión.
Misma decisión y revisión no añade evento. Runtime no puede actualizar, borrar
ni truncar evidencias. FK compuestas y revisión única preservan referencias de
empresa y actor. Fallo de INSERT revierte la operación. La migración 0045 rechaza
bajadas si hay evidencia, para evitar borrado silencioso de autorizaciones.

## Aplicación inmediata al servidor

La consulta operativa toma el mismo lock de empresa que la administración, resuelve
sesión/membresía/usuario activos y consulta la última revisión de esta concesión.
No confía en cookies de rol, banderas de UI ni IDs de empresa enviados por cliente.
La revocación no necesita cerrar la sesión base: la siguiente transacción que
comprueba el permiso después de ese commit se rechaza.

Cambiar una membresía fuera de Solo lectura o desactivarla agrega una revocación
si tenía permiso, dentro de la misma transacción de cambio de rol/estado y su
auditoría. Reactivarla no restaura el permiso: necesita otra concesión explícita.
Los cambios globales de usuario futuros deben integrar sus propios contratos;
SQL arbitrario del owner no está cubierto por esta coordinación del servicio.

No se promete borrar datos ya descargados, impedir capturas o retirar de forma
instantánea una respuesta que ya estaba autorizada. La UI oculta la consulta al
salir de pestaña, revalida sesión/permiso al volver o actualizar y retira los datos
ante rechazo o cambio de contexto. No prolonga la sesión con polling automático.

## Proyección operativa separada

GET `/inventory/availability` permite q parcial de nombre/SKU, category_id, limit
(1..100) y after. Solo productos activos de la empresa autenticada. Por defecto
25, cursor UUID ascendente, extra fila para calcular siguiente página. Parámetros
extra/duplicados se rechazan; el texto se vincula como substring literal y no
interpreta `%`, `_` o barra invertida como comodines del usuario.

Cada producto expone exactamente id/name/sku/category/price/quantity, con precio
de dos decimales y cantidad de tres como cadenas. Producto sin saldo: cantidad 0.
La consulta SQL selecciona solo esas columnas y nunca StockBalance.value. No se
reciclan DTO administrativos de costos. Costos y valoración no llegan a esta UI.

GET `/inventory/availability/categories` consulta categorías activas propias,
con paginación. Un cursor ajeno/desconocido solo es una frontera de UUID y no
elimina el filtro de empresa. El buscador combina nombre/SKU y categoría.

GET `/inventory/availability/products/{product_id}/image` devuelve únicamente el
PNG vigente autorizado (204 si falta foto). No publica bucket, clave S3, URLs
firmadas, historial ni referencias ajenas. La guardia se ejecuta antes del acceso
al Object Storage y de nuevo después de I/O; producto desactivado o permiso
retirado durante la lectura impiden devolver sus bytes. Cliente limita tamaño,
valida PNG y contexto, usa URL de objeto y la revoca al retirar la ficha.

Todas las rutas requieren cabecera de intención y membresía esperada, además de
sesión válida; respuestas no-store y vinculadas al actor. La ruta administrativa
anterior de imágenes conserva su guardia admin. El booleano operational del
servicio es composición interna del adaptador, no un parámetro HTTP de autoridad.

## Pantallas y alcance

- `/permisos`: administrador revisa acceso, modifica la casilla de consulta y
  confirma. Disponible desde administración de membresías/empleados.
- `/inventario/consulta`: lectura operativa con filtros, paginación y fotos. La
  cuenta Solo lectura tiene el enlace en Inicio; el servidor decide si puede
  obtener datos. Un enlace visible no concede permiso.
- `/inventario` y `/catalogo` administrativos siguen restringidos como antes.

Las escrituras de empleados siguen pendientes. Esta entrega no cambia saldos,
valores, métodos de pago ni reglas comerciales. El siguiente bloque debe definir
perfil/capacidades apropiadas para escritura sin convertir Solo lectura en un
perfil que modifica stock, y aplicar autorización transaccional e idempotencia.
