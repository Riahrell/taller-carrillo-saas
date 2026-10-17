# Seguridad: revisión y puerta de paso

Prioridad del usuario (2026-10-16): seguridad por encima del plazo del inventario.
Revoca la propuesta de pasar a catálogo inmediatamente después de 2.15. No
afirmar seguridad absoluta, certificación ASVS, ausencia de vulnerabilidades o
preparación para producción por acumular pruebas funcionales.

## Alcance y amenazas

Activos: credenciales, sesiones, identidades globales, permisos por empresa,
auditoría y futuros datos comerciales/financieros. Fronteras: navegador no
confiable -> proxy -> API -> PostgreSQL; operador/secretos/backup son otra frontera.
El perfil admin es de una empresa: no convierte al titular en administrador de
identidades globales ni de otras empresas. El equipo/navegador controlado por
un atacante y el SQL arbitrario privilegiado siguen fuera de las garantías actuales.

Amenazas a probar explícitamente: suplantación/robo de sesión, CSRF, XSS,
repetición/concurrencia, cambio de empresa en otra pestaña, manipulación de rol,
IDOR entre tenants, escalada por correo conocido, errores/logs que exponen secretos,
abuso de hashing y recuperación que eluda permisos. Cada módulo nuevo debe
heredar estas comprobaciones; el frontend nunca determina autoridad.

## Evidencia y brechas actuales

| Control | Estado y evidencia | Trabajo pendiente |
| --- | --- | --- |
| Identidad y tenant del servidor | Probado en servicios y HTTP con PostgreSQL real; perfil/actividad se releen. | Aplicar y probar autorización en cada futuro módulo. No hay RLS ni defensa frente a SQL privilegiado. |
| Cookies, sesión y CSRF | Cookies privadas, expiración, revocación, origen exacto y prueba firmada; tests por proxy. Confirmación reciente integrada en 2.18. | Inventario de sesiones y cierre global operativos, factores adicionales. |
| Acciones administrativas | Admin de empresa, contexto esperado, transacción y último admin protegidos; no vincular cuenta existente por conocer correo. En 2.18, UI y confirmación reciente obligatoria en alta/cambio (incluso no-op); 428 revierte efectos. Alta comprueba nuevamente antes del commit. | Confirmación por contraseña no es MFA. Web Locks/BroadcastChannel coordinan clientes cooperantes, sin garantizar cancelación de efectos tras desconexión. MFA/recuperación siguen bloqueantes antes de inventario. |
| Contraseñas | Argon2id, longitud Unicode 15–128, sin composición artificial, secretos fuera de logs/Storage. | Política de contraseñas comprometidas/comunes y cambios seguros; no evaluada resistencia a phishing solo con contraseña. |
| Alta asistida | UI 2.15 sobre contrato 2.14, comprobación de contexto y secreto en campos no controlados. | Requiere confianza en equipo/admin. El navegador y sus extensiones/gestor de claves quedan fuera de la aplicación; no afirmar borrado criptográfico de strings JS. Evaluar incorporación por invitación de un solo uso en dispositivo propio. |
| Recuperación y MFA | Contrato 2.20 en mfa-recovery-contract.md; primitivas internas probadas en 2.21, sin MFA activo. TOTP admin, prueba fresca de buzón para incorporación y códigos offline; correo no desactiva MFA. | 2.22 persistencia/consumo transaccional; después emisión/resolución de sesiones, selección, promoción, reautenticación y UI/correo, con aceptación completa. TOTP no resiste phishing; passkeys pendientes. Decisión de recuperación excepcional y verificación antes de producción/inventario según alcance. |
| Auditoría | Altas/cambios de membresía en mismo commit; app no puede UPDATE/DELETE esos eventos. | Login/denegaciones/cambios de credencial, consulta autorizada, retención y alertas. No hay registro de seguridad completo ni monitoreo. |
| Transporte y navegador | Proxy mismo origen, CORS cerrado, flags de cookie según entorno, cabeceras básicas. | TLS público, CSP compatible con Next.js y revisión de historial/cache/bfcache. No hay CSP estricta o ensayo HTTPS de despliegue público. |
| Dependencias y secretos | 2.19 agrega Gitleaks en archivos/historial, pip-audit y npm audit obligatorios en CI. Herramientas fijadas, canary sintético y fallos cerrados. | Un aviso raíz dev-only (cuatro entradas npm), SEC-2026-001, aceptado temporalmente hasta 2026-10-31; visible y probado contra cambios de alcance. Pendientes corrección del proveedor, CVEs de imágenes/SO, actualización/SBOM. Ver security-scanning.md; no equivale a ausencia de CVEs. |
| Recuperabilidad | Copia privada local 2.13; formato/índice del archivo comprobado. | Restauración completa en entorno aislado y reconciliación; copias cifradas/externas y runbook antes de producción. |

## Orden de trabajo y bloqueo del inventario

1. 2.15 completado: formulario con pruebas de secreto/contexto, API y navegador aislado.
2. 2.16: contrato/servicio y guardia interna probados; ver `reauthentication-contract.md`.
   2.17 agrega adaptador HTTP con CSRF/origen, slots y rotación de cookies.
   2.18 agrega UI y guardia obligatoria dentro de alta/cambio, con pruebas de
   denegación/rollback y recorrido de navegador aislado. Detalles y límites en
   `deliveries/access-2.18.md`.
3. 2.19: escaneos automatizados de dependencias/secretos integrados; revisar
   SEC-2026-001 antes de 2026-10-31. 2.20 define contrato MFA admin/recuperación;
   2.21 implementa primitivas TOTP/cifrado/códigos offline. Siguiente 2.22:
   persistencia/consumo transaccional. Cambio/recuperación de
   credenciales y cierre global, conservando
   las fronteras de identidad global/administración de empresa. No resetear una
   cuenta global desde otro tenant ni exponer un bypass de soporte.
   El usuario solicita recuperación por correo: diseño en `email-recovery-design.md`.
   Requiere buzón previamente verificado y no reemplaza la recuperación de MFA.
4. Ampliar evidencia de seguridad: auditoría consultable/denegaciones, revisión de
   dependencias/secretos, defensas del navegador y restauración aislada de backup.
5. Revisar esta matriz y resolver los bloqueantes o registrar una limitación
   explícita con el usuario. Solo entonces comenzar 3.1, catálogo de inventario.

Los controles específicos de inventario (permisos de productos, SKU, movimientos,
idempotencia, costo y locks) se implementan y prueban en el hito 3, sin exponer
acciones del módulo antes de tener sus permisos. La puerta de producción añade
HTTPS, hardening, backups restaurables, monitoreo y revisión de toda la superficie.
No convertir una brecha marcada aquí en una función implementada por documentarla.

## Dominio y HTTPS acordados

El usuario informa haber adquirido taller-carrillo.com y apuntado su DNS al VPS.
Resolución y servicio todavía no verificados desde aquí. Comprar/registrar el nombre no configura
HTTPS por sí solo: el despliegue requiere DNS al servidor y un certificado TLS
válido administrado por el proxy. Desde aquí no se ha configurado o publicado
el dominio. No codificar IP pública u origen de despliegue en la aplicación;
el dominio informado queda solo como contexto documental y el origen va por entorno.

Antes de exponer acceso público: definir origen canónico HTTPS, configurar DNS,
automatizar emisión/renovación del certificado (ACME o servicio equivalente),
redirigir HTTP a HTTPS, verificar cookies Secure/HttpOnly/SameSite y CORS/CSRF
con el origen final. Probar certificado/cadena/renovación y ausencia de contenido
mixto. HSTS se configura después de comprobar HTTPS y su recuperación; no activar
preload/includeSubDomains sin comprobar todos los subdominios. Backend y DB
permanecen privados; los secretos del proveedor DNS/certificado no van a Git/chat.

El desarrollo puede usar HTTP exclusivamente loopback. La configuración actual
rechaza un origen HTTP en producción; eso es validación de configuración, no
prueba de un despliegue TLS ya hecho. No modificar ahora el proxy local para
simular seguridad pública ni aceptar avisos de certificados inválidos.

[Let's Encrypt: inicio y clientes ACME](https://letsencrypt.org/getting-started/)
explica la emisión automatizada. [HTTPS automático](https://caddyserver.com/docs/automatic-https)
documenta DNS/certificado/renovación como ejemplo de operación; consultar esa
referencia no implica sustituir el Nginx actual ni que ya exista TLS público.

## Referencias y alcance de la evaluación

Usar ASVS 5.0.0 como base de requisitos verificables, apuntando a controles
adecuados para un sistema con datos privados y finanzas. Todavía no se ha realizado
una evaluación completa requisito por requisito ni una revisión independiente;
esta tabla es un registro inicial de amenazas/brechas del proyecto.

- [OWASP ASVS](https://owasp.org/projects/asvs): base para definir y probar controles.
- [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html): reautenticación en funciones sensibles, contraseña y factores.
- [OWASP Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html): recuperación sin convertirla en bypass de autenticación.

Consultadas el 2026-10-16. Las decisiones de este proyecto son una aplicación de
esas referencias al alcance actual; no implican que todos sus controles existan.
