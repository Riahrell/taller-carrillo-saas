# MFA y recuperación: contrato de diseño 2.20

Fecha: 2026-10-17. **Contrato 2.20 con primitivas 2.21, persistencia 2.22, auditoría 2.23 y cuotas 2.24.**
Login y confirmación administrativa siguen usando contraseña; no hay MFA activo,
correo verificado, recuperación operativa o proveedor de correo. 0009 agrega
tablas MFA y 0010 evidencia global de consumo, vacías en desarrollo, sin
habilitar factores en cuentas operativas. 0011 añade cuotas MFA internas
separadas de contraseña, sin integración de login.
La biblioteca no se invoca desde rutas públicas; 2.29 añade entrada interna de
autenticación con pendientes MFA, aún sin adaptación HTTP/UI. Ver
[entrega 2.21](deliveries/access-2.21.md) para evidencia y límites ejecutados.

## Decisiones y autoridad

TOTP mediante aplicación autenticadora será el primer segundo factor, conforme
al alcance de v1.1. No necesita un proveedor SMS o un servicio de identidad de
pago. El titular deberá disponer de una aplicación compatible y guardar códigos
de recuperación fuera de la aplicación. QR y semilla se entregarán directamente
al titular por el canal HTTPS de configuración, nunca por correo o soporte.

TOTP y códigos manuales no son resistentes al phishing. Passkeys/WebAuthn quedan
como ampliación prioritaria de seguridad; no declarar cumplimiento AAL2, ASVS o
seguridad absoluta por implementar este contrato. NIST exige ofrecer una opción
resistente al phishing para evaluar AAL2: [SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html).

El factor pertenece al **usuario global**, no a una empresa. Si el usuario tiene
un factor activo, se exige en todos sus accesos, incluso como mecánico en otra
empresa. Para administradores es obligatorio: tener una membresía admin activa
en una empresa activa impide obtener sesión solo con contraseña, aunque se
intente elegir otra membresía. La obligación se conserva al retirar ese rol;
no se desactiva MFA como efecto indirecto de un cambio de empresa o permiso.
El MVP permite sustituir/recuperar un factor, sin un interruptor para desactivarlo.

Una concesión de admin puede endurecer el requisito global de autenticación del
titular, pero nunca permite al admin concedente elegir su semilla, cambiar su
correo, ver códigos o restablecer su identidad. La incorporación deberá explicar
ese efecto al titular. La autoridad sobre roles/empresa continúa en el servidor.
No exponer al admin de una empresa la configuración de seguridad de otras cuentas.

Al integrar MFA, la protección de último admin no podrá contar como sustituto a
una cuenta cuya incorporación del factor esté pendiente. Conservar al menos un
admin con usuario/membresía activos y factor confirmado antes de retirar al
último que lo tenga. Probar junto con alta/promoción e incorporación concurrentes;
no debilitar esa garantía por crear otra fila con rol admin. Es un endurecimiento
de elegibilidad futuro, no un cambio de las reglas actualmente ejecutadas.

## Estado de autenticación y sesión

Estados lógicos internos; no son endpoints ni valores JSON ya disponibles:

| Estado | Lo que acredita | Siguiente transición permitida |
| --- | --- | --- |
| Sin autenticar | Nada | Verificar contraseña bajo límites existentes. |
| Contraseña verificada, MFA requerido | Primer factor y navegador ligados a un pendiente | Verificar TOTP; sin datos de empresas o cookie de sesión. |
| Configuración necesaria | Contraseña válida, obligación sin factor activo | Verificar buzón y autorizar alta restringida de TOTP. |
| MFA verificado | Ambos factores, revisión de seguridad y pendiente vigentes | Elegir membresía actual o emitir sesión si solo existe una. |
| Selección pendiente | MFA completo cuando corresponde, sin empresa elegida | Consumir una selección autorizada dentro del mismo plazo. |
| Sesión completa | Identidad, membresía y pruebas actuales del servidor | Funciones autorizadas; cambios sensibles exigen prueba reciente. |
| Recuperación restringida | Contraseña y código offline válido | Configurar factor sustituto; sin acceso operativo. |

Para cuentas sin factor ni requisito admin, se conserva el login actual. Ningún
camino interno, incluido authenticate con empresa seleccionada, podrá omitir la
comprobación central al emitir sesión. No basta que un endpoint compruebe MFA.
issue_session y resolve_session deberán validar prueba persistida/revisión actual;
no aceptar un booleano mfa_verified o un rol enviado por el navegador.

El login MFA normal y la selección comparten **cinco minutos desde verificar la
contraseña**, sin renovar plazo por cambiar de pantalla o emitir otro pendiente.
Tokens opacos de 256 bits, solo hashes persistidos, propósito y navegador ligados;
consumir/rotar en cada transición. Un pendiente de MFA, alta, selección o
recuperación no autoriza otro propósito ni una sesión de negocio. La empresa y
permisos se releen al finalizar, después de locks; revocación/promoción concurrente
invalida pruebas antiguas. Cambiar cookie o perder respuesta no habilita replay.

Logout/cancelar debe cerrar también los nuevos pendientes ligados al navegador.
Mantener Origin/CSRF firmado, proxy confiable, cookies privadas y rechazo de
cookies ambiguas/mixtas, límites de cuerpo y ausencia de secretos en errores.
Los contratos de rutas/cookies/respuestas se cerrarán en su entrega HTTP; aquí
no se agregan rutas, parámetros o cambios de las respuestas actualmente vigentes.

## TOTP, límites y prueba reciente

Parámetros del proyecto: HMAC-SHA-1, semilla aleatoria de 160 bits por factor,
seis dígitos ASCII preservando ceros iniciales, pasos de 30 segundos y tolerancia
de un paso anterior/siguiente. Se elige este perfil por interoperabilidad;
HMAC no es almacenamiento SHA-1 de contraseñas. Implementar con bibliotecas
mantenidas/verificadas y vectores de [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238).
La biblioteca y sus versiones se elegirán/auditarán en 2.21 antes de añadirlas.

La tolerancia admite hasta tres contadores candidatos; no prometer que cada
código solo pueda aceptarse durante 30 segundos. Reloj del servidor leído tras
locks, nunca fecha/counter aportados por cliente. Rechazar coincidencias ambiguas
con varios contadores. Persistir el mayor contador aceptado y exigir uno superior
en **todos** los usos del factor, entre procesos, navegadores y propósitos.
Verificar y consumir deben formar una transacción; el cálculo puro no autoriza.
Rechazar retroceso del reloj y probar sincronización operativa antes de producción.

Cuota MFA persistente separada de la cuota de contraseña: cinco fallos por cuenta
y treinta por IP en quince minutos, compartida por verificación, confirmación,
sustitución y recuperación del factor. No obtener intentos nuevos al recrear
pendiente, acertar contraseña o cambiar de propósito. Mantener cuotas separadas
para solicitudes de correo y protección de volumen del proxy. El éxito completo
puede limpiar fallos de cuenta de su cuota MFA, sin borrar historial de IP.
La implementación interna 2.24 elige conservar ambos hasta vencer la ventana: un
acierto de factor aislado no es éxito completo. Sin método de reset en esta fase.
No bloquear permanentemente al titular ni dormir workers para simular backoff.

En operaciones sensibles, exigir contraseña y TOTP recientes del mismo titular,
sesión y revisión; conservar contexto esperado, ETag, último admin y atomicidad.
La prueba combinada caduca a los cinco minutos desde **la más antigua de ambas
verificaciones**. No ampliar la ventana con selección de empresa o actividad.
Reautenticar rota cookies sin ampliar la vida absoluta de ocho horas.

Decisión de UX futura: un login completo con MFA puede establecer esa prueba
reciente en la sesión, tras elegir contexto. Así no se pide inmediatamente otra
contraseña/TOTP para el primer acceso administrativo. La interfaz podrá consultar
vigencia informativa del servidor; toda escritura seguirá comprobándola en DB.
Focus/visibilidad/avisos entre pestañas retiran borradores y revalidan contexto;
si la prueba sigue vigente no es preciso pedir otro código. Al caducar se exige
confirmación explícita y revisión nueva, sin reenviar una operación. Esto modifica
en el futuro la UX conservadora de 2.18; hoy no existe esa reutilización ni MFA.

## Alta inicial, sustitución y recuperación

### Incorporación del factor

La contraseña por sí sola no autoriza vincular el primer factor de un admin.
Exigir control del buzón exacto registrado y prueba de contraseña fresca en un
flujo restringido. La dirección se verifica por desafío, sin backfill de correos
actuales; para autorizar la incorporación se exige también prueba fresca de ese
buzón, no basta una marca histórica. No aceptar que soporte/admin sustituya la
dirección. Entregar/verificar la semilla por HTTPS después de esa autorización.

El flujo restringido de incorporación tiene un máximo total de quince minutos
desde verificar contraseña, incluyendo prueba de buzón y QR. Si vence se reinicia
explícitamente. Lecturas/escáneres de enlaces de correo no consumen permisos ni
activan factores. Con SMTP/prueba de buzón indisponibles, no hay fallback a una
sesión admin por contraseña ni activación por una cookie antigua.

Se activa solo después de verificar un TOTP de la semilla pendiente; consumirlo,
activar factor, generar códigos offline, invalidar sesiones/pendientes anteriores
y auditar en un commit. Sin login automático tras configurar: volver al login
normal, explicando que un TOTP consumido requiere esperar al siguiente código.
Una incorporación fallida no debe reemplazar un factor activo ni dejar acceso
administrativo parcial.

### Códigos offline y pérdida de teléfono

Generar **diez códigos independientes de 128 bits**, Base32 sin padding, de un
solo uso. Guardar SHA-256 con separación por propósito/usuario/conjunto; no
guardar códigos recuperables en DB. Su fortaleza proviene de aleatoriedad, no de
ser contraseñas cortas. Mostrar una vez por canal protegido después de commit;
no enviar por correo, logs, URL o Storage. Si se pierde esa respuesta, no
revelarlos de nuevo: el titular con factor activo deberá regenerar el conjunto.
El usuario debe guardarlos fuera del sistema antes de depender de ellos.

Contraseña válida y un código offline sin usar solo permiten iniciar recuperación
restringida, no obtener sesión de negocio. Consumir ese código, cerrar sesiones
y pendientes y emitir un permiso de recuperación ligado al navegador en un commit.
Ese permiso dura quince minutos desde su emisión y solo permite vincular/probar
un factor sustituto. El factor anterior permanece activo hasta reemplazo final;
no se desactiva al solicitar recuperación. Si el permiso se pierde, otro código
sin usar permite empezar de nuevo, bajo las mismas cuotas.

Al confirmar TOTP nuevo: sustituir factor, invalidar **todo** el conjunto anterior,
emitir conjunto nuevo, incrementar revisión de seguridad, revocar sesiones y
pendientes y auditar atómicamente. Volver a login completo. Una autenticación
normal exitosa con factor actual cancelará recuperaciones pendientes, bajo los
mismos locks. Las carreras se resolverán mediante revisión/estado releídos.

Sustituir voluntariamente un factor o regenerar códigos exige contraseña y TOTP
actuales frescos; no confiar solo en una sesión abierta. No regenerar códigos
para otra persona desde administración de empresa. Reglas de cambio/recuperación
se apoyan en [OWASP MFA](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).

### Contraseña olvidada y recuperación excepcional

El enlace de correo permite establecer contraseña nueva y revocar sesiones,
pendientes y otros **enlaces de contraseña**. Mantiene factor y códigos offline
MFA sin usar; estos se vinculan a la versión del factor, no al hash de contraseña.
No invalidarlos automáticamente al restablecer contraseña: impediría recuperar
teléfono perdido después de recuperar contraseña. El siguiente login sigue
exigiendo TOTP o el flujo restringido con código offline.

Perder teléfono y códigos offline no se resuelve con solo correo o intervención
de otro admin de empresa. Antes de producción se necesita runbook de recuperación
excepcional con prueba independiente de titularidad y registro/auditoría. No se
define una contraseña maestra, backdoor, SQL de desactivación ni bypass para el
último admin. Compromiso de cuenta/buzón requiere evaluación específica, fuera
del flujo ordinario de olvido. Ver [recuperación por correo](email-recovery-design.md).

## Persistencia, cifrado y transacciones futuras

Modelo lógico objetivo, sin nombres/campos de API. 2.22 materializa solo
credenciales y conjuntos/códigos en 0009; 2.23 añade evidencia de consumo en
0010. El resto de eventos y controles sigue pendiente. Ver
[persistencia y consumo](mfa-persistence-contract.md).

| Componente | Datos y restricciones que deberá representar |
| --- | --- |
| Estado de seguridad del usuario | Revisión monotónica, obligación MFA persistente, correo exacto verificado y fecha. Cambios solo por servicios autorizados. |
| Credencial MFA | Propietario global, identidad/versión de factor, semilla cifrada, key id, nonce, fechas y último contador consumido. A lo sumo un factor activo por usuario. |
| Pendientes tipados | Token/binding en hash, propósito/etapa, identidad, revisión y huella de contraseña, límites absolutos, estado consumido; nunca autoridad sobre una empresa. |
| Conjunto/códigos offline | Factor y usuario propietarios, hash por código, conjunto vigente, consumo/revocación; unicidad y uso único atómicos. |
| Prueba en sesión | Factores/contexto verificados y revisión vigente; null/ausente no satisface el requisito. Prueba reciente ligada a ambos factores. |
| Desafío de correo | Propósito, destinatario exacto y revisión, token hash, caducidad/uso único. No intercambiar autorización de incorporación y reset de contraseña. |
| Eventos de seguridad | Registro global append-only de alta/cambio/recuperación/rechazo y revisión; separado de audit_events comercial por tenant. Sin secretos o falso actor admin. |

La semilla TOTP debe poder verificarse: cifrarla con **AES-256-GCM**, nonce de
96 bits nuevo por cifrado, keyring versionado fuera de DB/Git/imagen y AAD con
propósito, usuario e identidad de credencial. Copiar ciphertext a otro usuario
debe fallar. Bibliotecas mantenidas; sin criptografía casera. Falta de clave,
tag inválido o versión desconocida produce rechazo/indisponibilidad saneados,
sin desactivar MFA. Definir rotación y respaldo recuperable de claves antes de
activar cuentas reales. Esta entrega no genera claves o semillas operativas.

Revisión de seguridad y estado actual se comprobarán en cada emisión/resolución
de sesión y consumo de pendiente. Activar/sustituir factor, promover a admin una
cuenta sin obligación previa o cambiar contraseña invalida sesiones/pendientes
de **todas** sus empresas; no altera roles/membresías ajenas. La concesión de
admin deberá invalidar esas pruebas en la transacción de cambio existente.

Conservar orden buckets IP/cuenta -> empresa cuando corresponda -> usuarios UUID
ordenados -> credenciales/pendientes/códigos -> sesiones. Una operación global
de factor toma usuario antes de hijos y no intenta obtener después locks de
empresas. Revisar juntos login, selección, roles, logout y reautenticación antes
de integrar, incluyendo orden de hijos, para evitar ciclos. Reloj leído tras
esperas. Contadores de fallo sobreviven al rechazo; secretos, consumo y eventos
no se confirman parcialmente. Error de commit no publica sesión/códigos.

Los avisos de seguridad se encolan con el cambio/evento; un fallo posterior de
entrega se reintenta sin repetir el cambio de credencial. Si la cola persistiera
un enlace, cifrar su contenido y eliminarlo tras caducidad/entrega. Antes de
activar envíos hay que cerrar contrato de entrega, retención y operación. No
confundir aceptado por proveedor con recibido por el titular.

## Despliegue y aceptación

Se puede entregar biblioteca/persistencia internas sin declarar MFA disponible.
La activación obligatoria se hará al integrar servicio, HTTP, UI, correo verificado
y recuperación, con pruebas end-to-end. Sin flag operativo que permita volver a
contraseña sola para saltarse un error. Sesiones anteriores no reciben prueba
MFA por backfill: revocarlas al activar el requisito. Preparar incorporación del
primer admin y backups de claves antes, sin manipular su contraseña real.

Un rollback no puede ejecutar una versión que ignore MFA con cuentas ya activas.
Restaurar backup puede resucitar códigos consumidos: el runbook deberá invalidar
sesiones/pendientes/conjuntos offline restaurados y restablecer un acceso seguro
antes de abrir tráfico. No declarar restauración segura por recuperar tablas.

Criterios de integración pendientes. 2.21 prueba primitivas y 2.22 añade
persistencia/consumo único concurrente y rollback, como operaciones internas.
2.23 añade evidencia global en el mismo commit y 2.24 cuotas internas compartidas,
con fallos persistentes y ventanas probadas. Otros eventos, integración de
sesiones/pending/HTTP/UI y operación siguen pendientes:

1. Vectores RFC, ceros iniciales, tolerancia/límites exactos, reloj regresivo,
   coincidencia ambigua, repetición y dos verificaciones concurrentes.
2. Cifrado descifrable solo con clave/AAD correctos, rotación, tag alterado,
   ausencia de clave y ausencia de secretos en repr/errores/logs/build/Storage.
3. Ningún camino de sesión única, selección multiempresa, servicio interno,
   cookie antigua o elección de perfil menor elude MFA exigido. Prueba de
   login no prolonga ventana reciente ni permite reutilizar contador.
4. Fallos/cuotas persistentes entre procesos/propósitos; acertar contraseña o
   recrear pendiente no limpia intentos MFA. Commit fallido revierte consumo.
5. Prueba de buzón/alta pendiente no autoriza negocio; escáner de enlaces no
   consume; caducidad y recuperación tras perder respuesta no crean bypass.
6. Promoción/revocación/reset/sustitución concurrentes invalidan todas las pruebas
   afectadas. Un admin ajeno no ve ni cambia el factor global del titular.
   Otro admin con incorporación MFA pendiente no permite retirar al último
   admin con factor confirmado.
7. Dos consumos de código offline producen un solo éxito; permisos restringidos,
   conjunto viejo invalidado al sustituir, email reset conserva MFA/códigos sin
   usar y nunca inicia sesión. Audit/credenciales/revocación son atómicos.
8. UI distingue contraseña, TOTP y código offline; no conserva secretos;
   protección de pestañas/contexto/CSRF/proxy, QR y recuperación completos con
   identidades sintéticas. HTTPS, correo, reloj y restauración probados antes
   de habilitar acceso público. Sin certificación AAL/ASVS inferida.

## Próxima tarea pequeña

2.30 implementa desafíos de buzón ligados a incorporación/navegador y permiso de
uso único, con auditoría y caducidad original. No envía correo, marca una dirección
histórica verificada ni activa factor/sesión. Ver [entrega 2.30](deliveries/access-2.30.md).

**2.31: entrega de correo con outbox cifrada, cuotas y transporte de pruebas**,
antes de SMTP real/HTTP. Después incorporación TOTP completa, recuperación,
hooks de roles/factores y último admin, reautenticación MFA, UI y operación.
Mantener el alcance de la puerta de seguridad previa al inventario.
