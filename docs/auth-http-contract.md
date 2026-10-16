# Contrato HTTP de acceso

Estado: acceso 2.8 conecta formulario, selección y logout al contrato HTTP de
acceso 2.7. Probados con PostgreSQL, Nginx/Uvicorn y recorrido real de navegador
con identidades sintéticas en una base efímera. No hay rutas comerciales ni
registro público de usuarios. Acceso 2.10–2.11 añade listado/consulta/cambio de membresías
existentes solo para administradores: [contrato específico](membership-http-contract.md).

## Rutas y contratos

El navegador usa el prefijo `/api`; Nginx lo retira antes de FastAPI. Todas las
respuestas de estas rutas llevan `Cache-Control: no-store`. No hay token en JSON,
URL o localStorage. CORS permanece cerrado.

| Navegador | Entrada | Respuesta |
| --- | --- | --- |
| GET /api/auth/csrf | Cabecera X-Requested-With: XMLHttpRequest | 204 sin cuerpo. Emite o renueva CSRF y, si hace falta, nonce previo al login. |
| POST /api/auth/login | JSON estricto: email, password | 200 con status authenticated, o 202 con status select_membership y choices de membership_id/tenant_name. |
| POST /api/auth/select-membership | JSON estricto: membership_id UUID | 200 con status authenticated tras consumir identidad pendiente y emitir sesión. |
| GET /api/auth/session | Cabecera X-Requested-With: XMLHttpRequest y cookie de sesión | 200 con user_id, membership_id, tenant_id, tenant_name y role actuales del servidor. |
| POST /api/auth/logout | JSON estricto vacío: {} | 204 sin cuerpo; revoca sesión y pendiente ligado al navegador antes de borrar cookies. |

Las escrituras exigen Origin exactamente igual a PUBLIC_ORIGIN y X-CSRF-Token
igual a la cookie CSRF firmada. Sec-Fetch-Site, si existe, debe ser same-origin.
Solo application/json UTF-8 hasta 8192 bytes, también para cuerpos fragmentados.
En las rutas de la tabla, campos extra se rechazan: ningún tenant, rol, IP, precio
o stock se toma del JSON. El cambio de rol autorizado de administración tiene su
contrato específico, sin aceptar el rol solicitado como autoridad del actor.
Credenciales únicamente en el cuerpo del login, nunca query strings.

Las lecturas de acceso exigen la cabecera personalizada X-Requested-With con valor
exacto XMLHttpRequest. Origin puede faltar en un GET del mismo origen; si existe,
debe coincidir exactamente. Sec-Fetch-Site, si existe, debe ser same-origin.
Una web de otro origen necesita preflight para esa cabecera y no obtiene permiso
CORS. No emitir cookies con imágenes/enlaces/formularios simples de otro sitio.
Estas cabeceras protegen al navegador; clientes HTTP externos pueden fabricarlas,
pero no obtienen por ello cookies privadas ni identidad de otro usuario.

| Rechazo | Código y cuerpo |
| --- | --- |
| Credenciales, cuenta/empresa/membresía inactivos, selección no autorizada o límite de fallos | 401, detalle Access denied, sin distinguir causa ni Retry-After de cuenta. |
| Origen/CSRF/cabeceras ambiguas/salto de proxy inválidos | 403, detalle Request rejected. |
| Validación Pydantic | 422, detalle Invalid request, sin inputs ni contraseñas. |
| JSON inválido/tipo incorrecto/tamaño excesivo | 400/415/413, detalle Request rejected. |
| DB, hashing/capacidad o resolución del proxy indisponibles | 503, detalle Authentication unavailable, sin SQL, credenciales ni traceback. |
| Límite general de volumen/conexiones en Nginx | 429, detalle Request rejected; independiente de identidad. |

Login/selección requieren preautenticación y ausencia de cookie de sesión. Para
cambiar de cuenta/empresa se debe cerrar la sesión primero. Una cookie de sesión
caducada o revocada se recupera solicitando CSRF: se comprueba DB, se retiran las
cookies antiguas y se inicia preautenticación nueva. No cambiar tenant en una
sesión existente ni seleccionar silenciosamente la primera empresa de varias.

## Cookies y ciclo de navegador

En producción HTTPS, los nombres llevan prefijo __Host-, Secure, Path=/,
SameSite=Lax y ausencia de Domain. HTTP solo loopback development/test, con nombres
sin prefijo y Secure desactivado. No extender la excepción a producción.

| Nombre base | Lectura por JS | Función |
| --- | --- | --- |
| carrillo_session | No, HttpOnly | Token opaco de sesión; solo su SHA-256 en DB. |
| carrillo_preauth | No, HttpOnly | Nonce firmado del servidor; protege intención antes de login, no acredita identidad. |
| carrillo_login | No, HttpOnly | Token opaco de identidad verificada pendiente de empresa, máximo cinco minutos. |
| carrillo_csrf | Sí | Prueba firmada HMAC-SHA-256 que se copia a X-CSRF-Token. No contiene token de sesión. |

Solicitar CSRF antes del login. El adaptador verifica la firma del nonce HttpOnly
y lo pasa como browser_binding al servicio; nunca usa un campo del cuerpo ni el
CSRF legible como binding. Con varias empresas, conserva ese nonce y emite la
cookie privada de selección. Renovar CSRF durante el selector conserva un nonce
vivo para mantener el binding. Si caduca, se descarta la cookie pendiente y se
reinicia preautenticación; el token anterior también caduca en DB a los cinco minutos.

Con una empresa o selección válida: sesión y consumo confirman antes de emitir
cookie; se retiran preauth/pendiente y se reemplaza CSRF ligado a la nueva sesión.
La sesión dura como máximo ocho horas y vence con 30 minutos de inactividad;
la consulta de sesión comprueba estados actuales y actualiza actividad en DB.
Renovar CSRF de una sesión requiere comprobarla y confirmar su actividad antes
pero no extiende su expires_at. CSRF/nonce duran como máximo 15 minutos.

Logout es idempotente para tokens conocidos, desconocidos o ya revocados, bajo
origen/CSRF válidos. Si están presentes sesión y pendiente con nonce válido,
revoca ambos en una transacción, bloqueando usuarios por UUID. Solo cancela un
pendiente con binding correcto. Después borra las cuatro cookies con el mismo
scope/atributos. Fallo de commit devuelve 503 y conserva cookies para reintentar.
La idempotencia de revocación no elimina la comprobación de origen/CSRF.

Tokens y pruebas no proporcionan idempotencia para operaciones comerciales. CSRF
no sustituye sesión/autorización ni protege contra XSS. Clave externa aleatoria de
32 bytes compartida por workers, sin reserva ni NEXT_PUBLIC. Rotación coordinada
y relojes sincronizados: firmas usan reloj UTC del proceso y sesiones/reto, DB.

## Identidad provisional y autoridad

begin_login verifica contraseña bajo bloqueo del usuario, límites compartidos y
estados activos antes de consultar las opciones. Cero empresas rechaza; una emite
sesión; varias devuelven opciones de membresía tras commit. El cliente propone
membership_id; usuario se deriva del pendiente, tenant/rol de la membresía actual
que le pertenece. No se consulta la lista por un correo sin autenticar.

Pendiente: token aleatorio de 256 bits, hashes de token/binding/hash Argon2 en DB,
vida de cinco minutos y un intento con binding correcto, incluso si la opción es
inválida. Otro navegador no puede usarlo ni consumirlo. Un nuevo login correcto
cancela pendientes anteriores del usuario; una contraseña incorrecta no los
cancela. Sesiones ya emitidas mantienen su ciclo de revocación independiente.
Cambiar hash de contraseña, incluso por rehash posterior, invalida un pendiente;
el propio rehash de begin_login se aplica antes de guardar la huella.

Consumo y emisión de sesión son atómicos. Bloqueos: IP -> cuenta -> usuario ->
pendientes por UUID -> sesiones. Releer tras esperar y usar reloj DB posterior al
lock. Acceso 2.9 coordina cambio de rol/estado de membresía, revocaciones y auditoría
en un servicio interno, sin rutas HTTP nuevas. Prebloquea empresa y todos los
usuarios implicados antes de resolver la sesión; detalles en decisions.md.
Los futuros cambios globales de cuenta/contraseña deben compartir ese protocolo.
Sin triggers
sobre SQL manual y sin RLS. Cada operación comercial tendrá sus propios permisos
y protocolo de bloqueo, aún no implementados.

## IP confiable, red y límites técnicos

Compose separa edge (frontend/proxy), api interna (proxy/backend) y private interna
(backend/DB/herramientas). Backend no comparte edge con frontend ni publica puerto.
Nginx sobrescribe X-Real-IP con su conexión observada y elimina Forwarded,
X-Forwarded-For y X-Forwarded-Host del cliente. No hay real_ip_header que adopte
cabeceras externas. La API no usa X-Forwarded-For para identificar al cliente.

Uvicorn arranca con --no-proxy-headers para conservar el socket peer real, con
--no-access-log para no registrar queries, y límite de 64 conexiones/tareas.
TRUSTED_PROXY_HOST obligatorio para HTTP identifica al servicio proxy mediante
DNS Docker en la red api. La API resuelve en cada login y comprueba que el socket
peer coincide antes de aceptar un único X-Real-IP válido. DNS fallido rechaza sin
reserva. IPv4 y IPv6 mapeada comparten clave de límite.

La pertenencia a redes/DNS/daemon Docker es autoridad del operador. Esto no prueba
identidad de hosts arbitrarios que controlen el daemon. No habilitar confianza
para todas las IP privadas ni adaptar este diseño a una cadena de proxies sin
un nuevo contrato y pruebas del salto real. HTTPS público sigue pendiente.

Perfil inicial: cinco fallos por cuenta y treinta por IP en 15 minutos, compartidos
en PostgreSQL. Un éxito reinicia cuenta, conserva fallos de IP. Nginx limita acceso
a 10 peticiones/s por IP, burst 20 sin espera y diez conexiones activas
por IP. Dos verificaciones de contraseña simultáneas por worker; exceso devuelve
503 sin cola de hashing. Cada transacción de acceso tiene lock_timeout 3s y
statement_timeout 10s. No son un timeout total de HTTP/DNS ni un benchmark de
capacidad. Medir y ajustar para producción; mantenimiento de contadores pendiente.

Logs Nginx: IP, método, URI sin query y status; sin cuerpos ni cookies. Uvicorn sin
access log. Los errores de request en /api/auth/ se descartan porque Nginx puede
incluir queries en ese log; se conserva el access log de status con URI saneada.
CI envía un canario sintético en cuerpo/query/cabeceras, también al provocar 429,
y examina los
logs capturados sin imprimirlos; falla si lo encuentra. No registrar dataclasses
con secretos ni volcar errores/headers de requests.

## Validación y pendientes

TestClient con PostgreSQL cubre cookies, refresh, pertenencia, estados DB,
repetición, fallos de commit y cierre atómico con cookies mixtas. CI separado usa
Nginx/Uvicorn y PostgreSQL reales: IP falsificada no cambia bucket, conexión directa
rechazada, sesión/logout/replay y selección multiempresa. No es aún una prueba de
interfaz ni HTTPS real; flags Secure se verifican con el perfil HTTPS de tests.

Siguiente: pantalla de administración, altas/cuentas, permisos de módulos,
ampliación/consulta de auditoría, recuperación y limpieza. El servicio interno de
membresías ya protege al último administrador; no cubre aún cambios globales de
usuario. Hito 2 abierto; producción e inventario/comercio pendientes.

Referencias: [CSRF OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
[redes Docker Compose](https://docs.docker.com/compose/how-tos/networking/).
Opciones Uvicorn contrastadas con el CLI instalado de la dependencia fijada.

## Cliente Next.js (acceso 2.8)

Primero GET session con X-Requested-With. Solo ante 401 prepara CSRF y muestra
credenciales. POST renueva CSRF, lee carrillo_csrf o __Host-carrillo_csrf según
protocolo y lo envía en X-CSRF-Token; no lee cookies privadas ni elige Origin/Cookie.
Requests relativos /api, same-origin, no-store y redirects rechazados. Solo campos
del caso de uso: email/password, membership_id o {}. Respuestas JSON decodificadas
y roles/UUID comprobados; no conservar propiedades arbitrarias.

Contraseña en campo no controlado, borrada al enviar y sin estado React/Storage.
Preservar espacios y Unicode; contar caracteres, no unidades UTF-16. El HTML usa
POST y el formulario solo aparece tras comprobación de sesión y preparación CSRF.
Validación nativa de email/requeridos; errores sin cuerpos internos y foco en avisos.

Un mutex en el componente y controles deshabilitados impiden doble envío desde
esa pantalla. No promete idempotencia entre pestañas. Cada request se cancela
tras 15s; abortar cliente no deshace servidor. No reintentar automáticamente POST.
Ante fallo de red/respuesta/403/429/503, ocultar contexto y ofrecer comprobar sesión
mediante GET. Cierre incierto permite reintento explícito de logout idempotente.

No elegir primera empresa, ni crear sesión visible solo por recibir status de
login: confirmar contexto con GET session. Un selector rechazado/caducado vuelve
a credenciales. Renovación CSRF preserva el nonce vivo según contrato servidor.
Cancelar selector usa logout, revocando el pendiente antes de volver al formulario.

Foco/visibilidad/pageshow y señales de BroadcastChannel motivan nueva lectura;
se aborta/descarta una lectura anterior. El canal solo transmite changed, sin
datos ni autoridad. No hay polling que mantenga viva una sesión inactiva. Timers
locales retiran selector a 5 min y contexto visible a 30 min, sin llamadas de
fondo ni sustituir los plazos/controles DB. Recargar o volver a una selección
pendiente exige volver a iniciar acceso; no se guardan opciones en Storage.

Teclado: labels, autocomplete username/current-password, grupos de radio sin
selección inicial, botones nativos, foco en título de selector/sesión y avisos,
busy/disabled y avisos alert/status. El perfil mostrado es informativo; los
permisos de negocio se implementarán y verificarán en backend.
