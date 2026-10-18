# Recuperación de acceso mediante correo

Decisión solicitada por el usuario el 2026-10-17. Recuperación de contraseña
aún sin formulario, endpoint o migración propia; no hay proveedor/envío activo.
Escaneos integrados y contrato conjunto en `mfa-recovery-contract.md`.
2.30 implementa solo desafíos internos de buzón para incorporación MFA, no reset
de contraseña ni envío. Ver `deliveries/access-2.30.md`. No se acreditan correos
verificados por backfill ni se intercambian los permisos de ambos propósitos.

## Alcance acordado

Recuperar acceso cuando se olvida la contraseña mediante el correo previamente
verificado de la cuenta. El correo ya identifica al usuario global: el proceso
no elige empresa, cambia permisos ni reactiva accesos deshabilitados.
Un administrador de empresa no puede recuperar la identidad global de otra
persona por conocer su dirección. Perder acceso al buzón o al segundo factor
requiere un procedimiento distinto, que no se sustituye con preguntas de seguridad.

## Flujo y controles

1. «Olvidé mi contraseña» solicita el correo y responde de forma genérica, exista
   o no una cuenta elegible. Limitar solicitudes y mensajes por cuenta/IP, sin
   bloquear el login de la víctima por peticiones de recuperación.
2. Para una cuenta elegible, enviar un enlace aleatorio, de un solo uso y con
   caducidad. No enviar contraseñas. Obtener el origen desde configuración HTTPS
   canónica, nunca de Host o una URL proporcionada por el solicitante.
3. Abrir el enlace muestra el formulario; una lectura o un escáner de correo no
   consume el permiso ni modifica la cuenta. El usuario confirma una contraseña
   nueva conforme a la política existente.
4. Cambiar contraseña, consumir el permiso, invalidar otros enlaces de
   restablecimiento de contraseña, sesiones y autenticaciones pendientes del usuario global y
   registrar el evento en una misma transacción. Fallo de commit revierte todo.
5. Avisar del cambio sin incluir contraseña y exigir login normal. Con MFA
   habilitado, restablecer la contraseña conserva el requisito del segundo factor.

Precisión 2.20: conservar también códigos offline MFA sin usar; pertenecen al
factor, no al hash de contraseña. El correo no regenera/consume esos códigos ni
desactiva MFA. Perder teléfono y contraseña permite primero restablecer contraseña
y luego iniciar recuperación restringida con un código offline, si se conserva.

Estos controles se apoyan en [OWASP Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
Recuperar MFA requiere su propio contrato: [OWASP MFA](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).

## Decisiones del proyecto

- Token opaco con 256 bits de aleatoriedad y duración propuesta de 15 minutos,
  medidos por reloj del servidor. Es una elección del proyecto, no un plazo
  exigido por OWASP. Persistir solo su hash en el registro de validación.
- El envío será asíncrono para desacoplar respuesta y proveedor. Si una cola
  persiste el enlace, su contenido secreto debe estar cifrado con clave fuera
  de la DB y eliminarse al completar/caducar; no guardar tokens en claro en outbox.
  El contrato de entrega debe contemplar retrasos, fallos y respuestas ambiguas.
- El secreto no debe filtrarse a logs, analítica, Referer o almacenamiento del
  navegador. Cerrar el intercambio HTTP y su política de URL/cache antes de
  implementar; no introducir ahora rutas o campos de API especulativos.
- Verificar el correo con prueba de control del buzón vinculada a una sesión
  autenticada y al valor exacto de la dirección. Los correos actuales **no se
  marcarán verificados mediante backfill**. Resolver incorporación/verificación
  antes de habilitar recuperación para esas cuentas; solicitar recuperación no
  verifica por sí mismo una dirección ni autoriza sustituirla.
- Cambio de correo, cuenta comprometida, pérdida del buzón y recuperación de
  MFA quedan fuera de este flujo básico y necesitan pruebas de identidad propias.
  No crear una contraseña de soporte o un bypass para el último administrador.
- Mantener monolito modular, locks/revocación coordinados con acceso existente
  y efectos globales de contraseña sin alterar membresías de ninguna empresa.

## Entrega y aceptación futuras

Primero contratos de correo verificado, MFA/recuperación y persistencia; después
servicio transaccional con PostgreSQL, transporte HTTP, formulario y envío.
Las primeras pruebas usan proveedor simulado y cuentas sintéticas; ningún correo
real se envía como efecto de este documento.

La aceptación deberá probar: respuesta sin enumeración y análisis de tiempos;
cuotas/abuso; correo no verificado; token desconocido, caducado y repetido; dos
consumos concurrentes con un solo éxito; lectura por escáner sin consumo;
rollback completo; revocación en todas las empresas; cuenta/membresía inactiva
sin reactivación; MFA sin bypass; logs sin secretos y fallo del proveedor.

Para activar envíos reales se necesitarán proveedor transaccional, remitente
validado del dominio, DNS de autenticación del correo y origen público HTTPS.
El dominio por sí solo no proporciona el servicio de envío. Proveedor/remitente
y costes se concretan al integrar, sin solicitar claves por chat ni contratar
servicios ahora. Tener el proveedor pendiente no bloquea el diseño/pruebas locales.

## Validación histórica de la decisión inicial

En la entrega inicial, solo documentación. Comando ejecutado: `git diff --check`; resultado exit 0,
sin errores de whitespace. No se ejecutan ni se atribuyen pruebas funcionales
nuevas a este diseño.
La entrega ejecutable vigente es 2.35; «Olvidé mi contraseña» sigue sin estar
disponible. 2.31 añade staging cifrado/cuotas para incorporación, aún sin emisor.
2.32 añade consumidor con transporte simulado, sin envío real.
2.33 confirma TOTP/códigos internamente; reset de contraseña sigue pendiente.
2.34 inicia recuperación con código offline, sin usar correo como segundo factor.
2.35 confirma el factor sustituto con TOTP nuevo, sin usar correo como segundo factor.
Resultados de esta base interna en `deliveries/access-2.35.md`.
