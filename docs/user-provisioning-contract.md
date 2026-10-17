# Alta administrativa interna (acceso 2.13)

Esta entrega añade un servicio interno, sin endpoint, formulario ni registro
público. El operador inicial sigue usando bootstrap. No ejecutarlo sobre cuentas
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
existente por un reintento. El futuro adaptador debe aplicar CSRF/origen, límites
de cuerpo/volumen y evitar logs/cache de entrada. Debe resolver entrega/elección
de contraseña mediante un canal controlado; este servicio no envía credenciales
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
