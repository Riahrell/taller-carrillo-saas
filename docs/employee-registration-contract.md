# Registro por invitación y aprobación — 3.41

El administrador prepara una invitación para un correo desde `/empleados`, tras
verificar su acceso. Comparte manualmente el enlace privado con ese destinatario.
El enlace usa `/registro#invitacion=...`: el secreto no se envía en URL/query al
servidor, se retira del historial al montar la página y no se persiste en storage.
Los correos de verificación se generan al solicitar el registro; no se envía una
invitación por correo automáticamente.

## Registro y verificación

1. El empleado abre la invitación en un navegador sin sesión activa, escribe el
   correo destinatario y una contraseña propia de al menos 15 caracteres, conforme
   a la política vigente (Argon2id y rechazo local de contraseñas comunes).
2. POST `/auth/employee-registration` acepta únicamente email/password/invitation.
   Empresa y correo deben coincidir con la invitación bloqueada en servidor,
   activa, sin revocar y dentro de sus 24 horas. No se acepta tenant, rol o permiso.
3. Se prepara un intento de hasta diez minutos, limitado por la vigencia restante
   de la invitación. Guarda hash de contraseña, hash del token/browser binding y
   un sobre de correo cifrado. No se crea todavía un usuario ni membresía.
4. El worker entrega un código alfanumérico de seis caracteres, con propósito
   criptográfico EMPLOYEE_REGISTRATION, AAD independiente del login y HMAC ligado
   al ID del intento. Solo hay correo simulado en las pruebas de esta entrega.
5. POST `/auth/employee-registration/confirm` exige cookie opaca HttpOnly,
   browser binding vigente, código correcto, origen/CSRF y plazo vigente. Tras
   verificar el código se crea la identidad nueva y se borra el hash de contraseña
   provisional y el sobre. La identidad conserva su hash Argon2id en users.
6. El recibo muestra pending_approval, sin sesión ni membresía. GET
   `/auth/employee-registration/status` permite recuperar ese estado desde el
   mismo navegador mientras su cookie/binding sigan vigentes. No publica email,
   empresa, IDs de usuario ni opciones de empresas. Su estado puede ser
   awaiting_code, pending_approval o approved.

Cookie `carrillo_registration` (prefijo __Host- bajo HTTPS), HttpOnly, SameSite
strict, path=/ y vencimiento del intento. Nunca se devuelve el token en JSON.
El inicio/verificación del registro no crea un navegador confiable de quince días.
Tras aprobarse se usa el login normal con contraseña y su prueba de correo.

## Límites y reintentos

- Cinco códigos incorrectos por intento. Persisten entre reenvíos y reinicios del
  formulario mientras el intento no caduque. Alcanzar cinco borra código/sobre.
- Se limitan inicios/confirmaciones a 30 por IP en una ventana fija de 15 minutos,
  con contadores PostgreSQL confirmados antes de evaluar el intento. Los rechazos
  también consumen esa cuota; no depende de memoria de un worker.
- Reenvío POST `/auth/employee-registration/resend`: mínimo 60 segundos entre
  envíos al mismo intento, tres por cuenta/intento y veinte por IP en la ventana
  compartida de correo existente. La UI indica espera y respeta 429/Retry-After.
  Un 429 puede requerir esperar más si se agotó una cuota de ventana.
- Repetir start con invitación válida antes de verificar puede reemplazar el token,
  binding, hash provisional y código; conserva plazo y fallos del intento vigente.
  La reserva de cuota y rotación son atómicas: si falla, el código anterior sigue
  siendo válido. Tras caducar se permite otro intento dentro de la invitación.
- El worker reclama una entrega antes de I/O. Un resultado incierto no se reintenta
  automáticamente. La respuesta de una entrega anterior no pisa el estado de un
  reenvío nuevo; se compara la generación del hash del código.
- Una confirmación repetida del mismo recibo verificado devuelve su estado, sin
  crear otra identidad o membresía. El recibo no concede autoridad comercial.
- La contraseña se elimina del formulario al enviarla; no se guarda en estado
  persistente del navegador. El enlace administrativo se oculta al cambiar de
  pestaña. Si se pierde el enlace, se revoca la invitación y se genera otra.

## Aprobación administrativa separada

GET `/auth/employee-registrations?limit=25&after=UUID` lista únicamente solicitudes
con correo verificado, sin aprobar y sin revocar, de la empresa de la sesión.
Autorización admin vigente, cabecera de intención de navegador, paginación 1..100,
rechazo de parámetros extra/duplicados y respuesta vinculada al actor. No devuelve
hashes, contraseñas, códigos, token, identidad global ni otra empresa.

POST `/auth/employee-registrations/{id}/approve` acepta solo `{}`. Requiere admin
actual, confirmación de identidad vigente y membresía esperada. El servidor fija
role=read_only; el cliente no puede pedir admin u otro perfil al aprobar. Revisa
usuario activo, prueba de correo, invitación sin revocar y empresa autorizada.
Una verificación válida anterior al vencimiento puede aprobarse después: el plazo
limita el uso del enlace/código, no la decisión posterior del administrador.

Crea membresía activa, EmployeeApproval inmutable y AuditEvent membership_created
en una sola transacción. La aprobación se repite sin duplicar y se coordina con
los locks de empresa. Revocar la invitación bloquea una aprobación pendiente; no
desactiva cuentas ya aprobadas. Eso se hace desde administración de membresías.

READ_ONLY continúa sin módulos concedidos. El inventario y catálogo privados
siguen exigiendo administrador; no se reutilizan sus DTO de costos para empleados.
La siguiente entrega debe implementar concesiones concretas y vistas operativas
antes de afirmar que los empleados pueden consultar o modificar inventario.

## Alcance y límites actuales

- Primera incorporación de una identidad nueva. Un correo global ya existente no
  se adjunta, reactiva ni cambia de contraseña mediante esta invitación. El
  resultado genérico evita identificar cuentas de otras empresas. Vincular una
  cuenta existente requiere un flujo autenticado separado, todavía pendiente.
- La prueba de registro todavía no se integra como fuente de VerifiedMailbox para
  recuperación global. El primer login normal verifica el correo y habilita esa
  evidencia existente. Recuperar una contraseña olvidada antes de ese primer
  login continúa pendiente; no se agregó un bypass de recuperación.
- Pendientes expirados conservan hash Argon2id provisional y evidencia mínima;
  se requiere política de retención/limpieza antes de la operación productiva.
- El enlace copiado no funciona en otro dispositivo si PUBLIC_ORIGIN es localhost.
  Para pruebas móviles se necesita el entorno HTTPS accesible previsto.
- No se declara despliegue productivo listo: faltan permisos de empleados,
  aceptación manual y cierre operativo de HTTPS/backup/objetos.

Migración 0044 crea tablas de registro/aprobación. Las FK conservan las referencias de empresa,
usuario y autor. Runtime no puede modificar/eliminar/truncar aprobaciones. La
bajada con registros existentes se rechaza y exige un procedimiento que conserve
la evidencia. No modifica saldos, valores ni movimientos de inventario.
