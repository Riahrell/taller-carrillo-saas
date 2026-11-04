# Guía de uso

Esta guía describe los flujos del inventario y la V1 registrada. Consulta
[Estado y versiones](project-status.md) para saber cuáles contiene tu rama.

## Acceder y recuperar la contraseña

Entra al panel con tu cuenta. La aplicación indica si debes verificar el acceso
por correo o completar MFA. Si tienes acceso a varias empresas, selecciona la
correcta antes de trabajar. Registrarse no concede acceso a una empresa.

Si olvidaste la contraseña, utiliza la opción de recuperación del login y completa
el mensaje solicitado. La recuperación no elimina MFA ni inicia sesión por sí
sola. No compartas contraseñas, códigos, enlaces de recuperación ni cookies.

## Registrar y editar productos

1. Abre **Catálogo** y comprueba la empresa activa.
2. Agrega un producto con nombre, precio, categoría y SKU opcional.
3. Revisa los datos y confirma el registro cuando lo indique el formulario.
4. Agrega su fotografía desde cámara o galería, si el almacenamiento está configurado.

Puedes buscar por nombre o SKU y combinar la búsqueda con el filtro de categoría.
Las listas se cargan por páginas; utiliza sus controles para ver más resultados.

Editar el producto cambia sus datos comerciales. El precio de venta no modifica
las existencias ni el costo. Si otra persona modificó el mismo registro desde que
lo abriste, vuelve a consultar el estado actual antes de guardar.

Desactivar conserva el registro, las fotos y su historial. En la V1 que incluye
eliminación, solo el administrador puede eliminar un producto y únicamente cuando
no tiene existencias, movimientos ni compras vinculadas; queda evidencia de auditoría.

## Registrar cambios de existencias

| Operación       | Cuándo usarla                                    | Ejemplo                                            |
| --------------- | ------------------------------------------------ | -------------------------------------------------- |
| Entrada manual  | Agregar unidades con un motivo y costo unitario. | Entran 5 filtros a $3 por unidad.                  |
| Salida manual   | Retirar unidades indicando el motivo.            | Salen 2 filtros por uso interno.                   |
| Conteo o ajuste | Registrar la cantidad física final contada.      | El conteo real es 7, aunque el sistema mostraba 8. |

En una entrada o salida escribe las unidades que se mueven; en un conteo, la
cantidad final. No son campos intercambiables. Las cantidades admiten hasta tres
decimales y los costos unitarios hasta seis, según la operación.

Revisa producto, cantidad, motivo y costo antes de confirmar. El servidor verifica
el saldo vigente y rechaza salidas sin existencias suficientes. El historial
conserva el origen y la persona responsable de los movimientos.

**Una salida manual no registra una venta, factura ni pago.** La salida vinculada
a una venta u orden necesita ese módulo y su documento de origen.

El administrador puede configurar mínimos y consultar productos por reponer.
La reversa disponible compensa el último movimiento manual o ajuste cuando cumple
las condiciones del sistema; no borra el historial ni anula compras o ventas.

## Personal y permisos

El empleado registra y verifica su cuenta. El administrador gestiona su vinculación
a la empresa y concede los permisos correspondientes desde el panel.

| Perfil        | Alcance en el inventario revisado                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------- |
| Administrador | Catálogo, inventario y gestión de accesos de su empresa.                                                |
| Cajero        | Consulta o salidas manuales solo con concesión explícita y las condiciones de cada permiso.             |
| Solo lectura  | Consulta si tiene permiso vigente; no modifica existencias.                                             |
| Mecánico      | No recibe permisos generales de inventario; las operaciones de órdenes siguen fuera del alcance actual. |

Cambiar un acceso puede revocar sesiones. No compartas la cuenta administradora
para evitar dar permisos específicos.

## Publicar productos para clientes — V1

Esta función pertenece a la versión posterior al `main` revisado:

1. En Catálogo abre **Ficha para clientes** del producto.
2. Prepara nombre público, descripción y enlace.
3. Decide si mostrar el precio y marca la ficha como publicada.
4. Guarda y comprueba la vitrina sin sesión.

Solo se muestran productos activos, no eliminados y publicados de la empresa
configurada para el sitio. No se publica todo el inventario automáticamente.
Las fichas no exponen SKU, costos, márgenes ni cantidades exactas.
Los clientes no necesitan cuenta; la consulta comercial se dirige a WhatsApp.

## Qué hacer ante un error

| Aviso o situación                    | Acción                                                                  |
| ------------------------------------ | ----------------------------------------------------------------------- |
| Sesión vencida                       | Iniciar sesión de nuevo y comprobar la empresa activa.                  |
| No se pudieron cargar datos          | Comprobar acceso y conexión; no asumir que la contraseña es incorrecta. |
| El registro cambió desde la revisión | Consultar de nuevo y revisar los valores actuales.                      |
| El guardado puede haberse completado | Consultar el historial o la operación pendiente antes de repetir.       |
| Falta permiso                        | Solicitar la concesión correspondiente al administrador.                |

Para solicitar soporte, comparte el texto del aviso, la pantalla, el botón y la
hora aproximada. Oculta cookies, tokens y datos privados en capturas y registros.
