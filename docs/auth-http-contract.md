# Contrato de seguridad del adaptador HTTP de acceso

Estado: acceso 2.6 añade el servicio interno de selección multiempresa desde
identidad verificada. Acceso 2.5 implementó cookies, firma CSRF, guard de origen
global y saneamiento de validación. Las rutas que emiten cookies son exclusivas
de tests; **no hay login/logout ni emisión de CSRF públicos**. Las URLs y el
transporte de selección se cerrarán junto con sus handlers antes de habilitarlos.
No confundir el middleware de intención con autorización de sesión.

## Entradas y autoridad

- Credenciales: objeto estricto con email y password; sin user_id, role, precio,
  stock ni tenant. Normalizar correo, preservar contraseña, rechazar campos extra.
  No aceptar hashes de contraseña desde el cliente ni contraseñas en query strings.
- La IP procede de la conexión observada por el proxy confiable. Nginx debe retirar
  cualquier cabecera de IP suministrada por el cliente y establecer el valor de
  conexión real. La API solo confiará en ese dato desde el salto de red autorizado;
  no aceptar cadenas X-Forwarded-For arbitrarias ni IP en JSON. No activar el login
  sin una prueba de falsificación de cabeceras a través del proxy real.
- selected_tenant_id es un parámetro **interno** del servicio actual, no forma
  parte de Credentials. Solo propone una membresía: nunca sustituye comprobar
  contraseña, usuario, empresa y membresía activos en servidor.
- La interfaz no consultará la lista de empresas de un correo sin autenticarlo.
  Si hay una sola membresía activa se seleccionará desde DB. Para varias, el
  selector usa un estado provisional verificado por el servidor y vuelve a
  comprobar pertenencia al confirmar. El servicio interno ya está implementado;
  falta su transporte antes de permitir login multiempresa desde HTTP. No elegir
  silenciosamente el primer UUID ni aceptar un tenant arbitrario como contexto
  autenticado.
- La autenticación posee su transacción: None equivale a rechazo **ya confirmado**
  con contadores persistidos. No envolver el servicio en una unidad de trabajo que
  revierta por devolver error HTTP. Un token solo sale después del commit exitoso.

## Respuestas y errores

| Caso | Semántica HTTP requerida |
| --- | --- |
| Credenciales rechazadas, identidad inactiva, membresía no autorizada o límite alcanzado | 401 y mismo mensaje genérico, sin distinguir causa ni confirmar existencia. Sin Retry-After específico de cuenta. |
| Error de formato/JSON/campos extra | Error de contrato saneado; nunca serializar input de validación de Pydantic ni repetir contraseñas. |
| DB/hashing indisponible | 503 genérico; sin detalles de conexión, argumentos SQL ni tracebacks al cliente. |
| Origen o CSRF inválidos | 403 antes de ejecutar cualquier efecto de negocio. |
| Inicio de sesión correcto | Cookie emitida después de commit; token de sesión nunca en JSON ni localStorage. |
| Cierre de sesión | Revocación idempotente y borrado de cookie con el mismo path/atributos; respuesta sin confirmar existencia de un token. |

Todas las respuestas de acceso deben usar Cache-Control: no-store. No registrar
credenciales, cuerpos, cookies, encabezados CSRF ni tokens en logs de API/proxy.
Los límites generales del proxy, independientes de identidad, podrán usar 429;
no se confunden con los contadores de fallos por cuenta del servicio.

## Cookies, origen y CSRF

- Cookie de sesión de producción con prefijo __Host-, Secure, HttpOnly,
  SameSite=Lax, Path=/ y sin Domain. HTTPS obligatorio. Caducidad absoluta del
  servidor de ocho horas y comprobación de inactividad de 30 minutos por petición.
- Desarrollo HTTP solo en loopback: nombre sin prefijo __Host- y Secure desactivado
  únicamente por configuración development. No extender esa excepción a producción.
- Origen público exacto configurado por entorno y validado al arrancar. No derivar
  el origen permitido de Host/X-Forwarded-Host sin validar. En operaciones con
  efectos exigir Origin igual al configurado; rechazar null, ausente y distinto.
  Revisar Sec-Fetch-Site como defensa adicional. CORS cerrado al mismo origen.
- CSRF mediante double-submit firmado, ligado criptográficamente a la cookie de
  sesión. Antes del login se liga a un nonce de preautenticación generado por el
  servidor; así el propio inicio de sesión también queda protegido. Requiere clave
  externa aleatoria compartida entre workers; sin reserva, sin incluirla en Git ni
  variables NEXT_PUBLIC. Comparaciones de MAC en tiempo constante y plazos finitos.
- El navegador enviará la prueba CSRF en cabecera independiente; SameSite no la
  sustituye. Ninguna credencial de sesión se vuelve legible por JavaScript. Generar
  la prueba CSRF no renueva por sí solo el plazo absoluto de la sesión.
- Solo JSON para contratos de escritura de acceso, tamaño acotado por proxy y API;
  rechazar contenido de formularios simples que pueda eludir la protección prevista.
  GET/HEAD no realizan operaciones de negocio ni alta/baja de usuarios.

La política de firma/cookies y el guard de origen/CSRF ya están implementados y
probados. El arranque HTTP exige PUBLIC_ORIGIN y clave externa, sin reserva. El
middleware exige JSON UTF-8 hasta 8192 bytes y también cuenta fragmentos; Nginx
limita /api/ al mismo tamaño. Se rechazan Origin/CSRF/cookies relevantes duplicados
para evitar interpretaciones ambiguas. La prueba ligada a sesión tiene precedencia:
una cookie de sesión inválida no puede eludir el control usando un nonce preauth.

La clave se añade con setup_http a entornos existentes sin regenerar DB. Los
helpers de cookies solo deben usarse tras los controles/commit del handler; no
consultan DB y por tanto no comprueban revocación. El nonce de preauth solo prueba
intención y no significa que ya se verificó una contraseña. La selección de
empresa necesita además un estado provisional autenticado separado.

Las pruebas/nonce son reutilizables hasta vencer: no proporcionan idempotencia
ni impiden por sí solos duplicar una operación de negocio. El perfil de prueba
dura 15 minutos. Validar firma usa reloj UTC del proceso con
resolución de segundos: los hosts deben estar sincronizados. Rotar la clave exige
reinicio coordinado y deja inválidas pruebas anteriores. La vigencia de sesión
sigue siendo autoridad de PostgreSQL, con 30 minutos de inactividad y máximo 8h.

Pendientes antes de exposición: login/logout y emisor/renovador de prueba HTTP,
validación de sesión/roles en handlers, prueba de IP a través del proxy, límites
de volumen/espera y transporte multiempresa. El formulario tampoco está conectado.

## Servicio interno de selección (acceso 2.6)

`Authenticator.begin_login` recibe Credentials, IP confiable y `browser_binding`
SecretStr del adaptador. El binding será una cookie HttpOnly emitida y verificada
por servidor; jamás el CSRF legible, un campo del JSON o una prueba de identidad.
Comprobar su formato no verifica la firma ni la procedencia: el adaptador HTTP
deberá hacerlo además de comprobar origen/CSRF. Esta fase no expone ese argumento
ni emite cookies nuevas.

Tras contraseña válida y los tres estados activos: cero empresas rechaza de forma
genérica; una emite sesión de esa membresía; varias crean PendingLogin y devuelven
solo membership_id/nombre de las empresas autorizadas como opciones. Nunca se
consulta esa lista antes de verificar contraseña. Límites cuenta/IP y rehash se
mantienen. El contador de cuenta se reinicia al verificar credenciales y membresías;
el historial de IP no se borra. Resultados y tokens solo salen después del commit.

PendingLogin dura cinco minutos desde reloj DB, lleva token aleatorio de 256 bits
y binding al navegador mediante hashes; no es una sesión y no autoriza módulos.
El futuro transporte guardará el token en cookie HttpOnly con scope/flags de
seguridad; nunca serializar el dataclass completo a JSON ni persistirlo en JS.
Si se reemplaza la cookie usada como binding, la selección anterior deja de ser
utilizable desde ese navegador y habrá que reiniciar el acceso.

`select_membership` acepta únicamente el UUID candidato de membresía, token
provisional y binding verificados por el adaptador. Usuario se deriva del registro;
tenant/rol, de la membresía actual que le pertenece. Revisa vigencia, consumo,
binding, usuario, huella del hash de contraseña y empresa/membresía activos.
Otra cookie de navegador no consume ni utiliza el pendiente. Una selección con
binding correcto consume el único intento, incluso si propone una membresía
inválida: rechaza genéricamente y exige reiniciar login. No incrementa contadores
de contraseña porque no está verificando una contraseña en este paso.

Consumo y sesión confirman juntos; ante fallo de insert/commit se revierten ambos.
Un nuevo login correcto cierra pendientes anteriores del usuario, también desde
otro navegador; no revoca sesiones ya emitidas. Un rechazo de contraseña no
invalida una selección pendiente. No se cambia de tenant en una sesión existente.
El rol se relee y no queda congelado en las opciones mostradas.

Cambiar el hash de contraseña, incluso rehash de la misma contraseña, invalida
un pendiente previo. El propio rehash de begin_login sucede antes de guardar la
huella. Los futuros cambios de cuenta/contraseña deben revocar tanto sesiones
como pendientes en la transacción del cambio; `revoke_user_challenges` ya permite
lo segundo. No hay aún handlers de administración ni triggers sobre SQL manual.
Limpieza de registros y límites generales de volumen/espera siguen pendientes.

## Pruebas antes de habilitar acceso web (parcialmente cubiertas)

- Login correcto, error genérico, límites en varias instancias y cambios de estado.
- Cookie y CSRF: producción/development, prelogin, token ausente/ajeno/modificado,
  sesión revocada, origen falso/ausente/null, cliente de otro sitio y JSON inválido.
- IP real a través del proxy: cabeceras falsas no cambian el bucket de origen.
- Ningún cuerpo de respuesta, error de validación ni log incluye contraseña/token.
- Sesión y cookie solo se publican tras commit; fallo al confirmar no autentica.
- Selección multiempresa exclusivamente tras identidad verificada, sin acceso
  cruzado y sin caché/carrito de la empresa previa al cambiarla.
- Frontend con estados de carga/error, teclado/foco y sin credenciales persistentes.

Referencia: [prevención CSRF OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

Las pruebas de cookies/origen/CSRF y de saneamiento se ejecutan con ASGI real y
rutas solo de test. El smoke del proxy verifica rechazos 403 y tamaño 413; no es
todavía una prueba de login, de IP confiable ni de revocación HTTP.
