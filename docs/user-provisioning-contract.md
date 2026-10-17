# Alta administrativa (acceso 2.13–2.15)

La entrega 2.13 añadió el servicio interno, 2.14 lo conecta a HTTP y 2.15 añade
formulario asistido. Sin registro público. El operador inicial sigue usando bootstrap. No ejecutarlo sobre cuentas
reales como parte de pruebas o desde otra transacción.

## Entrada y autoridad

`MembershipAdministrator.create_user` recibe cookie privada de sesión, un
`UserCreation` con email/password/role explícitos y `expected_actor` (UUID de
membresía verificado por el adaptador). El tenant y permiso admin salen de la
sesión actual, dentro de la transacción. expected_actor es una precondición de
contexto, no identidad elegible ni autorización. No admite tenant_id, user_id,
actor_role, hash, is_active, precio ni stock en el modelo de entrada.

Correo normalizado y validado igual que login. Contraseña de 15–128 caracteres
Unicode, sin recorte; SecretStr, excluida de repr/serialización del comando.
Hash Argon2id con parámetros existentes, únicamente tras autenticar/autorizar.
Rol objetivo entre los cuatro perfiles existentes, sin predeterminado. Solo el
admin actual puede solicitarlo; elegir admin no otorga autoridad al llamador.
Cuenta y membresía nuevas activas según el servidor, sin login automático.

Solo correo global nuevo. Si ya existe, activo o inactivo, en esta empresa o en
otra, devolver el mismo `UserCreationConflict`, sin IDs, empresas ni estados de
esa identidad. No adjuntar usuarios existentes por correo ni sobrescribir sus
credenciales; una vinculación multiempresa futura requiere otro flujo con prueba
de control/consentimiento de la identidad. Este conflicto sigue revelando que el
correo no está disponible a un administrador autorizado; no es un buscador público.

## Transacción y concurrencia

Orden existente: bloquear empresa de sesión, usuario actor y sesión/pertenencia;
releer actividad y permiso admin. Verificar expected_actor antes de consultar
disponibilidad o calcular hash. Insertar usuario global con unicidad del correo,
membresía en la empresa del actor y evento de alta en un commit. Una colisión
concurrente no crea membresía ni cambia una identidad existente. Retornar solo
después del commit, sin token, hash, contraseña ni ID global del usuario.

No cambia membresías existentes ni revoca sesiones del actor. Crear un segundo
admin se coordina con el lock de empresa usado por la protección del último admin.
El hashing se hace con esos locks para no insertar usando autorización caducada
por un cambio concurrente. Medir capacidad antes de exponerlo a carga sostenida.

Auditoría tipada `membership_created`: antes inexistente (before_role y
before_active null), después rol solicitado/activo. Misma tabla append-only y
FK de actor/objetivo a esa empresa. Los eventos de cambio conservan antes no nulo
y estado distinto. No se guardan correo ni credenciales en el evento.

Fallo de hash/DB/commit: `AdministrationUnavailable` saneado y rollback íntegro.
Sin reintentos automáticos ni idempotencia general. Una respuesta perdida puede
ocultar un commit: revisar el listado antes de otra alta; nunca resetear la cuenta
existente por un reintento. El adaptador 2.14 aplica CSRF/origen, límites
de cuerpo/volumen y evita logs/cache de entrada. El canal asistido del futuro
formulario se define abajo; este servicio no envía credenciales
ni afirma invitaciones, correo verificado, cambio inicial obligatorio o recuperación.

## Esquema y aceptación

Migración 0007 amplía CHECKs de audit_events y hace nullable solo el estado previo.
Conserva eventos existentes y privilegios SELECT/INSERT de aplicación. Downgrade
a 0006 permitido únicamente sin eventos de alta; con ellos debe abortar sin
borrarlos ni inventar un estado anterior. No retirar evidencia para forzarlo.

Validar: entradas y secreto, todos los perfiles objetivo, denegación de actores
no admin/sesiones inválidas/inactivas, contexto distinto, duplicados propios/ajenos/
inactivos, hash/login del nuevo usuario, auditoría y permisos DB, rollback de hash,
inserción/auditoría/commit, dos altas simultáneas con mismo correo (incluyendo
empresas distintas), permiso revocado durante espera, último admin y migración
con evidencia anterior/de alta. Todo solo en PostgreSQL efímero protegido.

## Adaptador HTTP 2.14

POST `/api/auth/memberships` (FastAPI `/auth/memberships`), sin barra final ni
query params. Es una ampliación de la colección administrativa existente, sin
nueva colección de identidades globales. JSON con email/password/role obligatorios,
sin campos extra. Misma política de validación/secreto del servicio 2.13.

Exige cookie privada de sesión vigente, permiso admin actual, Origin exacto,
CSRF firmado ligado a sesión y `X-Expected-Membership` obligatorio (UUID canónico
en minúsculas de la sesión verificada). Sin cabecera: 428 Precondition required;
inválida/duplicada: 403 Request rejected; contexto diferente: 403 Access denied.
La cabecera solo compara contexto, no elige actor/tenant ni concede autoridad.
Una preautenticación o selección pendiente no autoriza alta. CSRF no sustituye
autorización. El cliente debe renovar CSRF antes de escribir.

201 solo después del commit, JSON membership_id/role/is_active. Cabeceras ETag
y X-Access-Membership igual que detalle; Location relativo
`./memberships/{membership_id}` para resolver correctamente desde colección con
o sin prefijo `/api`, sin confiar en Host o construir un origen absoluto.
No devuelve correo, ID global, contraseña, hash ni token. No emite/borra cookies
ni inicia sesión como la persona nueva. Actor conserva su sesión.

Errores saneados: 400 JSON inválido; 401 sesión ausente/inválida; 403 permiso,
contexto, origen o CSRF; 409 `User creation unavailable` para correo global ya
registrado; 415 tipo de contenido; 422 cuerpo/query inválidos; 428 contexto
requerido; 503 `Authentication unavailable` ante fallo DB/hash/commit o saturación.
Siempre no-store, sin ETag/Location/cabecera de actor en errores. Sin cambios de
cookies por alta o fallo; recuperación de sesión mediante rutas existentes.

JSON UTF-8 hasta 8192 bytes en middleware y proxy; límites existentes de
conexiones/volumen de `/api/auth/` (429) y dos slots Argon2 por proceso compartidos
con login, sin espera en cola al saturar. Logs sin cuerpo/cookies/query de acceso.
No añade throttles persistentes por alta ni cambia los del login. El lock de
empresa y la unicidad global siguen arbitrando concurrencia. POST repetido del
mismo correo da conflicto y no resetea la cuenta; no es idempotencia general ni
permiso de reintento automático. Ante respuesta perdida consultar listado/sesión.

## Canal de contraseña y límites del alta asistida

Canal del formulario 2.15: alta presencial/asistida en un equipo
controlado, con administrador autenticado. La persona nueva elige/escribe su
contraseña en un campo oculto sin estado persistente; el formulario debe borrarla
al enviar/cancelar y retirarla de memoria al perder contexto. Sin generar o
enviar claves por correo, chat, URL, archivos o argumentos de terminal. El dato
viaja solo al backend del mismo origen. HTTPS es obligatorio fuera del desarrollo
loopback, según la validación de configuración vigente.

Este canal requiere confianza en el equipo y en el administrador; ocultar el campo
no protege contra quien controla ese navegador/equipo. No promete invitación
remota, verificación de correo, contraseña temporal/cambio forzoso, recuperación
ni MFA. No adjunta cuentas existentes. Las pruebas usan únicamente credenciales
sintéticas en DB efímera.
La recuperación de acceso debe completarse antes de declarar listo para producción.

## Formulario 2.15

Ruta `/membresias/nueva`, montada solo tras consultar sesión admin actual. Primer
paso: correo y perfil explícitos sin predeterminado. Segundo: resumen sin clave y
campos ocultos de contraseña/confirmación sin estado React. Contar puntos de código
Unicode 15–128 y conservar espacios; volver a validar todo en backend. Campos
reseteados al enviar, editar, cancelar o desmontar por pérdida de contexto.

El transporte renueva CSRF, envía solo email/password/role y X-Expected-Membership;
no elige tenant, cookies u Origin. Requiere 201 y actor/rol/estado/ETag/Location
válidos. Sin reintentos, Storage, logs de payload o contraseña en el resumen.
Mutex impide doble envío y controles quedan deshabilitados durante la escritura.

Antes de mostrar éxito se confirma la misma sesión admin y se relee el detalle
creado. 409/422 retiran contraseña y exigen otra revisión; 401/403/428/429/503 o
fallo incierto retiran formulario/contexto. Focus/visibilidad/timer y señales
entre pestañas reutilizan el control de workspace. Abort no revierte un commit.
La reautenticación reciente/MFA todavía no existe en servidor: es una brecha
priorizada en `security-review.md`, no un control que este formulario sustituya.
