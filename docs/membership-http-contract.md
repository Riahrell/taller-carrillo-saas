# Administración HTTP de membresías existentes

Contrato de acceso 2.10–2.11. Extiende la operación interna 2.9 y sus reglas; sin altas,
registro público, cambios globales de usuario, exportación masiva ni pantalla todavía.
Los nombres de rutas y cabeceras son decisiones técnicas de este adaptador.

| Navegador | Entrada | Éxito |
| --- | --- | --- |
| GET /api/auth/memberships | Query opcional limit/after; cabeceras de lectura de acceso. | 200 con items y next_after, máximo 100 registros. |
| GET /api/auth/memberships/{membership_id} | UUID objetivo; cabeceras de lectura de acceso. | 200 con membership_id, role, is_active y cabecera ETag. |
| PATCH /api/auth/memberships/{membership_id} | JSON role/is_active explícitos, If-Match y origen/CSRF de sesión. | 200 con la misma representación y su ETag actual, tras commit. |

Solo un administrador con sesión vigente en la empresa seleccionada puede leer
o modificar. Se resuelve de nuevo en la transacción de cada operación. Objetivos
de otra empresa o inexistentes producen el mismo 403 que la falta de permiso.
Sesión ausente/inválida/inactiva/revocada/caducada produce 401. El pendiente de
selección y la preautenticación no acreditan sesión. Las rutas de detalle/cambio
no aceptan query params; el cuerpo solo admite los dos atributos del estado
deseado. No hay tenant, actor, hash, token o precios/stock como entradas. El listado
añade el correo existente para identificar al usuario, solo después de autorizar.

El rol solicitado no concede autoridad; exige permiso admin actual. La membresía
inactiva se puede consultar/reactivar, sin activar la cuenta global. El último
administrador utilizable sigue protegido por la transacción 2.9. No hay DELETE.

GET exige X-Requested-With: XMLHttpRequest; Origin, si existe, debe ser exactamente
PUBLIC_ORIGIN, y Sec-Fetch-Site, si existe, same-origin. PATCH exige Origin exacto
y X-CSRF-Token firmado ligado a la cookie de sesión; SameSite no sustituye CSRF.
JSON UTF-8 hasta 8192 bytes, con ambos campos obligatorios y booleano estricto.
El cliente renueva CSRF antes de escribir. Cookies privadas HttpOnly; CORS cerrado.

## Listado paginado (2.11)

Ruta canónica sin barra final: GET `/api/auth/memberships`. Solo `limit` y `after`
opcionales. limit predeterminado 25, entero decimal canónico ASCII entre 1 y 100;
sin ceros iniciales, signos, espacios ni decimales. after es un UUID, no una prueba
de autorización. Parámetros desconocidos, repetidos o inválidos devuelven 422 sin
eco de inputs. No se aceptan tenant, rol, offset, búsqueda ni orden del cliente.

Respuesta: `items` con membership_id/email/role/is_active/user_is_active;
`next_after` es el UUID del último elemento si se observó al menos otro registro,
o null al terminar. El correo proviene de users.email existente y user_is_active
refleja users.is_active, sin nueva columna ni acceso a su identidad global completa.
Se incluyen membresías/cuentas inactivas: reactivar una membresía no activa la cuenta.
No incluye hash, token, ID global del usuario ni información de sus otras empresas.

Solo la empresa de la sesión determina el filtro. Un cursor desconocido o de otra
empresa sirve únicamente como frontera UUID: no se consulta su existencia ni
concede acceso. Se usa ID > after, orden UUID ascendente y LIMIT limit+1 con el
índice compuesto existente (tenant_id, id), sin OFFSET ni COUNT global.
Una frontera posterior al último registro devuelve items vacío y next_after null.

Cada página vuelve a autorizar sesión/rol actual y confirma actividad antes de
publicar el resultado. Se mantiene empresa -> usuario actor -> sesión; los objetivos
se consultan sin bloquear sus usuarios después de la sesión del actor. El lock de
empresa serializa cambios coordinados con este servicio. Sin auditoría ni
revocaciones por consultar. Error de commit devuelve 503 sin publicar correos.

Las páginas son lecturas del estado actual, sin snapshot persistente, total ni
promesa de exportación consistente. Una inserción con UUID anterior al cursor puede
requerir reiniciar el listado para observarla. Cambios posteriores se reflejan en
la página que los lee. Sin ETag de colección ni cookies nuevas, no-store y sin 304.
Antes de editar, consultar el detalle para revisar el estado y obtener su ETag.

## Revisión y concurrencia

GET devuelve un ETag entre comillas con prefijo membership-v1- y digest SHA-256
de la representación serializada estable. Cada PATCH exige un único If-Match con
ese formato. No se admiten wildcard, tags débiles ni listas en este contrato
acotado. Duplicados o valor mal formado: 400. Falta la cabecera: 428. Las
validaciones sintácticas pueden preceder a la consulta DB y no revelan objetivos.

La comparación sucede después de autenticar/autorizar y bloquear el objetivo,
dentro de la misma transacción que el cambio. Si su estado actual difiere del
revisado: 412 sin cambio, revocación ni auditoría. Releer, revisar y decidir de
nuevo; no sobreescribir automáticamente. Si dos cambios distintos parten del
mismo estado, solo el primero puede confirmar con ese tag. Un estado ya idéntico
es no-op y conserva el tag sin revocaciones/eventos.

El ETag valida estado actual, no una revisión histórica monotónica ni prueba que
el cliente hizo GET: si el estado vuelve exactamente al anterior, vuelve el tag.
No es un secreto ni sustituye autorización/CSRF. No se usa para 304 ni caché.
El servicio interno conserva llamadas sin precondición para casos coordinados;
la ruta PATCH siempre valida y pasa If-Match, sin opción pública de omitirlo.

## Errores y efectos de sesión

| Estado | detail |
| --- | --- |
| 400 | Invalid precondition; Request rejected para JSON inválido. |
| 401 | Access denied. |
| 403 | Access denied por permiso/objetivo, Request rejected por origen/CSRF/cookies. |
| 409 | An active administrator is required. |
| 412 | Membership state changed. |
| 422 | Invalid request. |
| 428 | Precondition required. |
| 503 | Authentication unavailable, compartido por FastAPI y normalización Nginx de acceso. |

415/413/429 y límites de red/cuerpo conservan la política de acceso existente.
Respuestas de éxito/error no-store. GET/PATCH no emiten una sesión ni renuevan
cookies: el servicio comprueba/toca actividad en DB dentro de su commit.
Si un cambio efectivo retira o modifica el propio acceso, DB revoca su sesión y
la respuesta exitosa borra las cuatro cookies con el scope correcto. Un no-op
propio conserva cookies. El resultado no expone flags internos de revocación.
Un 401 no borra cookies desde estas rutas: recuperación mediante GET auth/session
o auth/csrf existente. Un fallo de commit preserva cookies y no devuelve éxito.

Un timeout o respuesta perdida puede ocurrir después de commit. No hay retry
automático ni promesa de idempotencia general. Consultar estado y sesión; si el
actor perdió permiso debe intervenir otro administrador. No volver a enviar una
escritura con un tag nuevo sin revisión. La respuesta es un snapshot confirmado;
otra operación posterior puede cambiarlo o revocar al actor.

Estas rutas viven bajo /api/auth/ para reutilizar el límite de volumen/conexiones,
normalización de fallos y logs sin query/cookies/cuerpo del proxy existente.
No se cambió su política de confianza en IP ni se agregó un puerto público.

Referencias: [If-Match, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-match),
[428, RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html#section-3),
[errores FastAPI](https://fastapi.tiangolo.com/tutorial/handling-errors/).
