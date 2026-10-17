# Confirmación reciente: servicio 2.16 y HTTP 2.17

## Alcance y estado

Servicio interno y guardia transaccional de 2.16; adaptador HTTP de 2.17 descrito
al final. Todavía sin formulario.
Las rutas administrativas actuales todavía no invocan esta guardia: **la brecha
de confirmación reciente continúa abierta** hasta conectar UI y operaciones
en la entrega siguiente. No activar un requisito imposible de satisfacer desde
la aplicación actual ni confundir esta base con protección operativa terminada.

La decisión responde a la prioridad de seguridad del usuario. OWASP recomienda
reautenticar funciones sensibles y renovar tokens tras reautenticación; ver
[Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
y [Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
La ventana de cinco minutos es una decisión conservadora del proyecto, no un
plazo impuesto por esas referencias. Repetir contraseña no constituye MFA ni
resistencia a phishing.

## Entrada y autoridad

`Authenticator.reauthenticate(token, data, expected_actor, client_ip)` recibe:

- Token privado de una sesión existente, nunca un ID que seleccione identidad.
- `PasswordConfirmation`: solo contraseña actual, 1–128 puntos de código Unicode
  UTF-8 válidos; no recortar ni transformar. Mantiene compatibilidad del login,
  sin imponer retroactivamente la longitud de alta a contraseñas existentes.
  `SecretStr`, excluida de repr y serialización del modelo; errores sin eco.
- UUID de membresía esperada, obligatorio: compara con sesión; no da autoridad.
- IP validada por adaptador/red de confianza; nunca desde JSON o cabecera sin
  verificar. La normalización IPv4/IPv6 y límites son los del login.

No admite email, usuario, empresa, rol, hash, token nuevo, fecha o duración en el
cuerpo. El usuario y la empresa provienen de la sesión; se releen actividad y
perfil admin. No crea acceso, selecciona empresa, recupera identidad, cambia
contraseña ni concede privilegios a un perfil no administrador.

## Transacción, límites y rotación

El servicio posee una transacción READ COMMITTED con límites de lock y statement
existentes. Orden de bloqueo: bucket IP -> bucket de cuenta -> empresa -> usuario
-> sesión. Login no toma lock de empresa; administración no toma buckets.
Las pistas leídas antes del lock no autorizan: se revalida estado después de él.

Comparte las tablas y ventanas persistentes de login: 5 fallos por cuenta y 30
por IP en 15 minutos, entre procesos. Una confirmación no ofrece otra cuota de
intentos de contraseña. El éxito limpia fallos de cuenta y conserva historial
de fallos de IP, igual que login. Token desconocido de formato válido consume
fallo de IP sin inventar una cuenta. Un rechazo sin hashing también puede
consumir cuota. Estos contadores permiten denegación temporal de acceso;
mantener límites de red/proxy y revisar política al añadir recuperación/MFA.

La verificación Argon2id usa el hash actual bajo lock de usuario, y hash dummy
si el almacenado es inválido. No cambia/rehash la contraseña en este camino;
el login conserva su rehash y una modificación del hash invalida confirmaciones.
Una contraseña errónea o un contexto inválido revierte el savepoint de sesión
sin tocar token, actividad o prueba previa; la transacción externa confirma los
contadores. Fallo DB/hash/commit devuelve error de infraestructura saneado y
revierte todo, sin publicar resultado parcial o hacer retry.

Tras verificar se comprueban otra vez los límites idle/absoluto y retroceso de
reloj. Un token nuevo aleatorio de 256 bits sustituye al hash del token anterior
en la misma fila y commit que fecha/huella de confirmación. Solo el hash del
token se persiste. Devuelve `IssuedSession` con `SecretStr` únicamente después
de commit; conserva IDs, empresa, membresía, created_at y expires_at originales.
La reautenticación no amplía las ocho horas, no revive sesión expirada/revocada
ni comparte confirmación con otro navegador o sesión de la misma persona.

El token viejo deja de servir inmediatamente, incluso para reautenticación.
No hay periodo de gracia ni entrega posterior del token nuevo. Si se pierde la
respuesta de rotación, iniciar sesión de nuevo; no repetir automáticamente.
Revocar usuario/membresía bajo el protocolo de locks incluye el token rotado.
Un logout con token viejo ya invalidado sigue siendo idempotente y no identifica
el token nuevo: el adaptador/UI deberán coordinar escrituras concurrentes y
notificar rotación entre pestañas. No se promete revocación de una familia de
tokens por una cookie antigua.

## Guardia dentro de la operación sensible

`require_recent_administrator(session, token, expected_actor=...)` se ejecuta
**en la misma transacción** de la operación, bajo el protocolo de empresa/usuarios
de administración. No usar una comprobación previa independiente ni aceptar
un booleano calculado en el frontend. La guardia no sustituye autorización de
objetivo, control de último admin, ETag, auditoría o atomicidad del cambio.

Relee sesión/perfil/membresía y exige prueba en esa sesión, reloj de servidor
`reauthenticated_at <= now < reauthenticated_at + 5 minutos`, sesión aún vigente
y huella SHA-256 del hash de contraseña actual coincidente. La huella no es un
token válido para autenticación y no sale por API.
Actividad ordinaria toca idle pero no renueva esos cinco minutos.

Sesión inválida o actor/perfil diferente: `AccessDenied`. Admin válido sin
prueba, con prueba caducada/futura o hash cambiado: `RecentAuthenticationRequired`.
El llamador debe salir con rollback ante rechazo; no capturarlo y confirmar
escrituras previas. Retorna contexto fresco del servidor, no permiso comercial.

## Persistencia y migración

Revisión `0008_session_reauthentication` agrega a `sessions` dos columnas nulas:
`reauthenticated_at` y `reauthentication_fingerprint`. Sin defaults/backfill.
CHECK exige ambas nulas o ambas presentes, huella hexadecimal de 64 caracteres
y fecha dentro de la vida absoluta. Sesiones anteriores y de login siguen
sin confirmación hasta completar este procedimiento explícitamente.

Downgrade a 0007 bloquea la tabla, revoca sesiones confirmadas aún activas y
retira columnas. Conserva sesiones ordinarias, identidades, membresías y auditoría.
Actualizar después no restaura pruebas ni revive sesiones revocadas. Respaldar
antes de migrar datos operativos; nunca ejecutar el downgrade como reparación
de login. No se elimina la protección de auditoría de la migración 0007.

## Conexión pendiente y aceptación posterior

Contrato HTTP 2.17 definido antes de su implementación: POST
`/api/auth/reauthenticate` por proxy, `/auth/reauthenticate` directo. JSON estricto
con solo `password`, sin query. Sesión privada actual, Origin exacto y prueba
CSRF firmada ligada a esa sesión. `X-Expected-Membership` obligatorio y UUID
canónico; solo compara contexto, no selecciona identidad. Rechazar cookies
mezcladas de sesión con preautenticación/selección pendiente. Sin sesión,
una preautenticación o identidad pendiente no autoriza confirmación.

Adaptador toma IP del proxy confiable y comparte dos slots Argon2 por proceso
con login/alta, sin esperar en cola al saturar. Límites persistentes de cuenta/IP
del servicio y límites Nginx de `/api/auth/` permanecen vigentes. Cookies ambiguas,
origen/CSRF o contexto mal formado se rechazan antes del servicio.

Éxito: **204 sin cuerpo**, `Cache-Control: no-store` y `X-Access-Membership` con
la membresía comparada por el servicio. `Set-Cookie` de sesión con nuevo token
y misma expiración absoluta, CSRF nuevo ligado a ese token, retirada de cookies
preauth/challenge. Cookie privada HttpOnly; Secure y prefijo __Host- en HTTPS,
SameSite=Lax y Path=/, sin Domain. Ni token, contraseña, huella, tenant, fecha o
duración en cuerpo/URL. Sin ETag o Location ni autoridad tomada de Host.

Rechazo de credencial/sesión/permiso/contexto vigente: **401 Access denied**
genérico, sin borrar cookies (evitar que un error tardío borre una rotación).
Otros rechazos: 400 JSON inválido/ambiguo; 403 Request rejected para origen,
CSRF, cookies/headers ambiguos o par remoto no confiable; 413 cuerpo excesivo;
415 tipo de contenido; 422 campos/query inválidos; 428 Precondition required
si falta membresía esperada; 429 del proxy; 503 Authentication unavailable
por slots/DB/hash/commit. Siempre no-store; sin Set-Cookie ni cabecera de actor,
ETag o Location en error. Rechazos previos al servicio no tocan sesión o contadores.

Límite JSON 8192 bytes. El middleware compartido rechaza claves JSON duplicadas,
sin eco del cuerpo, para evitar interpretación ambigua en escrituras existentes
y nuevas. El adaptador no expone la guardia ni agrega un bypass para escribir.
Una prueba CSRF previa no vale con token nuevo; un token previo no autentica.
Ante respuesta perdida, iniciar sesión de nuevo; no repetir la confirmación.
La protección del transporte debe probarse con PostgreSQL y proxy reales.

Siguiente 2.18: confirmación en frontend con campos no persistentes, pérdida de
visibilidad/contexto, rotación entre pestañas y resultado incierto sin replay;
activar guardia en alta y cambios dentro de su transacción, con denegaciones
sin efectos y recorrido completo. Mantener ejecutable cada entrega. No afirmar
que CSRF/slots HTTP se probaron para un endpoint que aún no existe.

MFA/passkeys, recuperación, auditoría de eventos de autenticación, limpieza de
throttles y revisión de seguridad general siguen pendientes en `security-review.md`.
No se requiere adquirir servicios para esta entrega interna.
