# Acceso por correo y navegador verificado

Decisión del propietario: sustituir el requisito de aplicación autenticadora
por contraseña y código numérico por correo, recordando el navegador 15 días.
Implementado localmente con la migración 0037_email_browser_login.
Validación funcional manual pendiente por el propietario; no se afirma que
la batería automatizada del contrato nuevo haya pasado.

## Comportamiento

- Contraseña obligatoria en cada inicio de sesión.
- Primer acceso desde un navegador: código por correo de exactamente seis
  dígitos, incluidos ceros iniciales. El mismo navegador puede omitir el correo
  durante 15 días desde su última verificación satisfactoria.
- El plazo es fijo: iniciar sesión o navegar no lo renueva. Al vencer se exige
  otra verificación. No equivale a una sesión de 15 días.
- Cookie opaca aleatoria, HttpOnly, Secure en producción y SameSite, separada
  de la cookie de sesión. Sin localStorage ni identificadores de dispositivo
  aportados por el cliente. La base almacena su hash y fecha límite.
- El permiso pertenece al usuario y navegador, no otorga tenant, perfil ni
  acceso a empresas. Cada acceso resuelve las membresías actuales en servidor.
- Otro navegador, cookie ausente o borrada, permiso revocado o vencido requieren
  nuevo código. Los permisos se invalidan con cambio/restablecimiento de
  contraseña, revocación global o cambio de revisión de seguridad.
- Cierre ordinario de sesión retira la sesión pero conserva la verificación
  del navegador hasta su vencimiento. Las operaciones sensibles mantienen su
  confirmación reciente; el permiso de 15 días no satisface esa confirmación.
- Correo enviado mediante la cola existente; nunca se registra el código ni
  se devuelve en la API. Solo se entrega a la dirección de la cuenta.

## Valores técnicos propuestos para la implementación

Código criptográficamente aleatorio, válido diez minutos, de uso único y ligado
a un intento posterior a contraseña válida. Máximo cinco verificaciones fallidas
por intento y límites acumulados por cuenta/IP, también entre intentos nuevos.
Verificador mediante HMAC con clave externa: un hash simple de seis dígitos es
insuficiente frente a enumeración offline. Payload de cola cifrado.

Conservar el mínimo existente de 60 segundos y tres envíos por cuenta cada
15 minutos, más límite por IP. El reenvío explícito reemplaza el código anterior
sin ampliar indefinidamente la vigencia del intento. No hay reenvíos automáticos.
Mensajes y contador describen la espera; no revelan existencia de cuentas.
Consumo del código y creación del permiso son atómicos y seguros ante concurrencia.

## Secuencia de entregas pequeñas

1. Migración y servicio interno de desafío y navegador recordado, sin habilitar
   una ruta alternativa que omita la protección actual.
2. Integración de contraseña, selección de empresa, sesión, correo y revocación;
   adaptar confirmación reciente y recuperación al nuevo método.
3. Interfaz de seis dígitos sin QR, despliegue local y guía manual.

No habilitar la modalidad hasta completar el conjunto: el modelo actual exige
una prueba ligada a credencial TOTP incluso al resolver sesiones. No se resuelve
desactivando mfa_required ni fabricando pruebas TOTP.

## Aceptación al habilitar

Primer acceso solicita correo; acceso posterior en el mismo navegador antes de
15 días omite correo pero exige contraseña. Otro usuario/navegador no hereda el
permiso. El día 15 se exige código sin renovación por uso. Código erróneo,
vencido, reemplazado, consumido o de otro intento se rechaza; doble consumo no
crea permisos adicionales. Cambio de contraseña/revocación invalida permisos.
Caída de correo nunca permite saltarse la verificación. La autorización de
inventario y las transacciones no se modifican.

Pruebas funcionales manuales a cargo del propietario conforme a su preferencia.
No se ha ejecutado la batería funcional automatizada ni el acceso manual por
parte del agente. Véase deliveries/email-login-3.21.md para resultados de
compilación y guía de prueba.
