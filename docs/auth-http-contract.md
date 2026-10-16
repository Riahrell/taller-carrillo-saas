# Contrato de seguridad del adaptador HTTP de acceso

Estado: diseño previo a implementación. Acceso 2.4 implementa autenticación interna
y contadores; **este contrato todavía no está conectado a HTTP**. No se añaden ni
se anuncian rutas nuevas en esta entrega. Las URLs y los contratos del selector
de empresa se cerrarán junto con sus handlers y pruebas antes de habilitarlos.

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
  selector deberá usar un estado provisional verificado por el servidor y volver
  a comprobar pertenencia al confirmar. Ese flujo y su transporte se implementarán
  antes de permitir login multiempresa; no elegir silenciosamente el primer UUID
  ni aceptar un tenant arbitrario como contexto autenticado.
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

Estos son requisitos de la siguiente implementación, no garantías del entorno
actual. Aún no hay clave CSRF, nonces, cookies de autenticación ni estos handlers.

## Pruebas antes de habilitar acceso web

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
