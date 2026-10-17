# Cuotas y verificación MFA interna: acceso 2.24

`MfaVerifier` es la fachada interna para verificar/consumir factores bajo cuota.
No está conectada a rutas, login, selección o reautenticación; no valida contraseña,
pendiente, revisión o autorización de recuperación y no emite sesiones. No usar
el resultado como prueba transferible. Complementa el contrato de
[persistencia/auditoría](mfa-persistence-contract.md), sin activar MFA operativo.

## Política ejecutable

- Cinco fallos evaluados por usuario global y treinta por IP en quince minutos.
  Al alcanzar cualquiera de los límites, se devuelve rechazo sin evaluar el factor.
  Ese rechazo no incrementa contadores ni desplaza el comienzo de la ventana.
- Cuenta identificada por UUID del servidor, no por empresa, correo o propósito.
  TOTP y recuperación usan las mismas filas; nuevos workers, IP o pendientes
  no crean presupuesto de cuenta nuevo. No hay parámetro de propósito/límite/hora.
- Tabla `mfa_throttles` separada de `login_throttles`. Acertar contraseña, recrear
  selección o modificar contadores de contraseña no afecta la cuota MFA.
- Ventana fija desde el primer fallo; en el límite exacto `inicio + 15 minutos`
  se renueva, al acceder, con hora de PostgreSQL. Nunca bloqueo permanente o
  sleeps de backoff. Éxito de factor conserva **ambos** historiales: por sí solo
  todavía no es autenticación completa. No se ofrece reset público o interno.
- IP validada como IPv4Address/IPv6Address por el llamador de confianza, obtenida
  del proxy verificado al integrar HTTP; no texto del body/X-Forwarded-For directo.
  IPv4 y su forma IPv6 mapped comparten clave; se descarta la zona de interfaz
  IPv6, que no representa otro presupuesto de origen. SHA-256 con dominios distintos
  para UUID/IP; no se guarda IP/correo en claro. El hash de IP es enumerable,
  no anonimización ni sustituto de una política de retención.

## Transacción y errores

Cada método posee su sesión/transacción y publica el resultado **tras commit**.
No llamarlo dentro de otra transacción de negocio. Orden de locks obligatorio:
IP -> cuenta -> usuario -> credencial -> conjunto/código, más evento de consumo.
UPSERT sin duplicar filas y FOR UPDATE con refresco ORM serializan procesos.
La hora se relee tras esperas; una regresión de reloj falla cerrada.

Un código/identidad/factor rechazado devuelve None y confirma sus fallos de cuota,
sin eventos de éxito. Consumo válido y evento global confirman juntos. Fallos de
DB, timeout, escritura de auditoría o protección de semilla generan
`MfaVerificationUnavailable` genérico y rollback completo, sin publicarlos como
rechazos guardados o cargar códigos incorrectos. No se devuelven parámetros SQL,
claves o datos del factor. No hay reintento automático con resultado incierto.
Timeouts de lock de tres segundos y sentencia de diez limitan las esperas; no son
promesa de latencia ni sustituyen límites de volumen/conexiones del proxy.

La fachada actual solo confirma cuota/consumo/evento. Al integrar pendientes y
sesiones, sus transiciones autorizadas deben incorporarse a la misma transacción
propietaria con revisión/locks actuales; **no** emitir sesión después en otra
transacción basándose únicamente en este resultado. No se expone callback libre
al llamador. Los consumidores de bajo nivel de 2.22 siguen disponibles para
composición interna/pruebas y no deben usarse directamente desde HTTP eludiendo
la cuota. Las futuras variantes de MFA deben pasar por la misma guardia.

## Esquema y operación

0011 crea `mfa_throttles`: scope cuenta/IP, key_hash SHA-256, inicio de ventana y
fallos entre cero y el límite de ese scope, con PK scope/hash e índice temporal.
Sin claves operativas, backfill o modificaciones de sesiones/cuentas. App sin
DELETE/TRUNCATE/DDL; el propietario separado queda fuera de esa garantía.
Downgrade rechaza cualquier cuota o factor, aun con contador cero o ventana
expirada, para no olvidar estado o volver a verificadores sin límite. No borrar
filas para forzarlo; recuperación/corrección operativa requiere procedimiento.

Faltan limpieza con retención y límites de cardinalidad, volumen del proxy,
cuotas de correo y observabilidad de rechazos. Una cuenta puede sufrir denegación
temporal si un atacante llega al flujo autorizado y agota sus intentos; no se
promete ausencia de ese riesgo. No hay auditoría de autenticación completa,
alertas, credenciales activadas o integración pública por crear estas cuotas.

2.25 implementa el kernel de estado/revisión global con revocación y auditoría.
La fachada aún no lo conecta a pruebas de sesión. Siguiente 2.26: pendientes MFA
tipados; ver `deliveries/access-2.25.md` para límites y resultados.
