# Administración HTTP de membresías existentes

Contrato de acceso 2.10. Extiende la operación interna 2.9 y sus reglas; sin altas,
registro público, cambios globales de usuario, listado masivo ni pantalla todavía.
Los nombres de rutas y cabeceras son decisiones técnicas de este adaptador.

| Navegador | Entrada | Éxito |
| --- | --- | --- |
| GET /api/auth/memberships/{membership_id} | UUID objetivo; cabeceras de lectura de acceso. | 200 con membership_id, role, is_active y cabecera ETag. |
| PATCH /api/auth/memberships/{membership_id} | JSON role/is_active explícitos, If-Match y origen/CSRF de sesión. | 200 con la misma representación y su ETag actual, tras commit. |

Solo un administrador con sesión vigente en la empresa seleccionada puede leer
o modificar. Se resuelve de nuevo en la transacción de cada operación. Objetivos
de otra empresa o inexistentes producen el mismo 403 que la falta de permiso.
Sesión ausente/inválida/inactiva/revocada/caducada produce 401. El pendiente de
selección y la preautenticación no acreditan sesión. No se aceptan query params;
el cuerpo solo admite los dos atributos del estado deseado. No hay tenant, actor,
correo, hash, token o precios/stock en el contrato.

El rol solicitado no concede autoridad; exige permiso admin actual. La membresía
inactiva se puede consultar/reactivar, sin activar la cuenta global. El último
administrador utilizable sigue protegido por la transacción 2.9. No hay DELETE.

GET exige X-Requested-With: XMLHttpRequest; Origin, si existe, debe ser exactamente
PUBLIC_ORIGIN, y Sec-Fetch-Site, si existe, same-origin. PATCH exige Origin exacto
y X-CSRF-Token firmado ligado a la cookie de sesión; SameSite no sustituye CSRF.
JSON UTF-8 hasta 8192 bytes, con ambos campos obligatorios y booleano estricto.
El cliente renueva CSRF antes de escribir. Cookies privadas HttpOnly; CORS cerrado.

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

Ambas rutas viven bajo /api/auth/ para reutilizar el límite de volumen/conexiones,
normalización de fallos y logs sin query/cookies/cuerpo del proxy existente.
No se cambió su política de confianza en IP ni se agregó un puerto público.

Referencias: [If-Match, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-match),
[428, RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html#section-3),
[errores FastAPI](https://fastapi.tiangolo.com/tutorial/handling-errors/).
