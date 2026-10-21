# Invitaciones privadas de empleados — 3.40

Primera entrega del registro autónomo: API administrativa de preparación y
revocación de invitaciones, ligada a un correo y a la empresa de la sesión.
Todavía no hay pantalla ni consumo público de la invitación. No crea usuarios,
membresías, solicitudes de incorporación ni permisos. La cuenta del empleado y
su aprobación se implementarán en la siguiente entrega; no se declara terminado
el registro ni habilitado inventario para empleados.

## API

Rutas del backend; el proxy público antepone `/api`:

- POST `/auth/employee-invitations`: solo `{ "email": "..." }`. Sesión admin
  vigente, confirmación de identidad vigente y X-Expected-Membership obligatorio.
  Devuelve 201 con `invitation` y `code`, únicamente después de commit.
- GET `/auth/employee-invitations?limit=25&after=UUID`: sesión admin actual y
  cabecera de intención de navegador. `limit` entre 1 y 100. Paginación por UUID,
  filtrada por la empresa autenticada, incluso con cursor ajeno o desconocido.
  Devuelve `items` y `next_after`. No requiere confirmación reciente para leer.
- POST `/auth/employee-invitations/{id}/revoke`: cuerpo `{}`, sesión admin,
  confirmación y membresía esperada. Repetir una revocación no añade otro evento.
  Invitación ajena o inexistente devuelve el mismo 403 sin representación.

Cada representación incluye id, email, created_at, expires_at y status
(pending/expired/revoked). No contiene tenant, hash, código ni identidades globales.
Las respuestas exitosas incluyen X-Access-Membership; todas son no-store.
Escrituras pasan por origen/CSRF, JSON limitado y rechazo de claves duplicadas.
Campos extra, parámetros desconocidos o duplicados se rechazan. El cliente
no elige empresa, actor, rol, vigencia ni estado.

## Decisiones de seguridad

- Código de 32 bytes aleatorios, codificado base64url (43 caracteres), separado de
  los códigos de seis caracteres del login. Este secreto prepara un futuro enlace
  privado; no es una contraseña, sesión, prueba de correo ni permiso de inventario.
- Solo se guarda su SHA-256; el código completo sale una vez en el 201. No se
  recupera en listados ni auditoría. No se manda correo en esta entrega.
- Vigencia fija de 24 horas desde el reloj de PostgreSQL. El listado calcula
  caducidad sin modificar evidencias.
- Una invitación sin revocar por correo/empresa, garantizada por índice único.
  Para volver a invitar, incluso si caducó, hay que revocar la anterior. Hasta
  100 invitaciones sin revocar por empresa para acotar acumulación pendiente.
- El lock de empresa de la guardia compartida coordina creación/revocación con
  cambios de acceso. Solicitudes concurrentes no duplican una invitación abierta.
- Crear y auditar, o revocar y auditar, suceden en la misma transacción. Un fallo
  de evidencia revierte la operación. Se vuelve a comprobar autorización antes
  de devolver el resultado. Rol revocado o sesión inválida no conceden operaciones.
- Evidencia tipada CREATED/REVOKED con empresa, invitación, autor y hora; runtime
  carece de UPDATE/DELETE/TRUNCATE sobre ella. En la invitación runtime solo puede
  actualizar revoked_at; tampoco puede borrar/truncar invitaciones ni sustituir email, empresa, autor, hash o fechas.
- Misma dirección en dos empresas no transfiere ni enumera las identidades
  globales. Crear invitaciones no consulta ni altera usuarios existentes.
- Respuesta perdida al crear: no se repite automáticamente ni se intenta recuperar
  el secreto. Consultar el listado, revocar y preparar otra. Conflicto 409 genérico.
  El límite y el índice también protegen frente a solicitudes repetidas.

Migración 0043: tablas nuevas y permisos mínimos. Una bajada con invitaciones
existentes se rechaza para preservar evidencia; no elimina historial silenciosamente.

## Siguiente integración

El futuro formulario consumirá una invitación válida sin aceptar empresa o rol,
verificará el correo y mantendrá la solicitud pendiente hasta aprobación admin.
No debe restablecer ni vincular una identidad existente con solo conocer su correo.
Aprobar incorporación y conceder permisos de inventario serán decisiones separadas,
auditoradas y denegadas por defecto. Esta API no habilita todavía esas acciones.
