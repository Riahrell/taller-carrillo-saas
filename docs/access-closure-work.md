# Trabajo continuo de cierre de acceso

## Cambio de prioridad — 2026-10-18

Por instrucción expresa del usuario se pospone la VPS y se continúa en local con
catálogo e inventario. Esto sustituye la puerta anterior que exigía terminar el
despliegue antes de comenzar módulos comerciales. No elimina los controles de
acceso de cada operación ni certifica seguridad productiva. El seguimiento
histórico de abajo conserva lo ocurrido; para estado remoto vigente consultar
`staging-plan.md`. Próxima tarea: contrato y persistencia mínima de productos,
con aislamiento por empresa y sin modificar existencias al crear un producto.

Solicitud vigente: implementar los recorridos pendientes, después ejecutar la
validación conjunta y corregir los fallos antes de pasar a inventario. No cerrar
por cantidad de pruebas. Este archivo es seguimiento, no evidencia de controles.

- [ ] Reset: consumo único, revocación global, auditoría y aviso transaccional.
- [ ] Verificación general del buzón ligada a sesión y dirección.
- [ ] Reautenticación MFA ligada a sesión, revisión y vida absoluta.
- [ ] Hooks globales de roles y elegibilidad del último administrador con MFA.
- [ ] HTTP: login/incorporación/TOTP/offline/reemplazo/códigos/reset/cierre global.
- [ ] UI de los recorridos y manejo de secretos/contexto/caducidad.
- [ ] Claves externas, consumidor de correo con deadline, purga y retención.
- [ ] Restauración completa aislada, runbook de claves/recuperación excepcional.
- [ ] Validación conjunta, regresión, navegador y revisión de brechas.
- [ ] Correo real y despliegue HTTPS: aceptación externa antes del cierre.

## Estado del 18 de octubre de 2026

Resend: dominio verificado según el usuario. SSH `contabo` comprobado mediante
inspección de solo lectura. El despliegue anterior publica frontend, API y
PostgreSQL en interfaces del servidor; no se ha comprobado acceso desde Internet
ni se ha cambiado configuración, datos o servicios remotos.

La implementación local de los recorridos anteriores y rotación voluntaria está
escrita. Migraciones 0025–0029 aplicadas con éxito en PostgreSQL efímero durante
validación. No aplicadas en desarrollo ni VPS. No constituye cierre de seguridad.

Evidencia parcial: Ruff pasa; lint y tipos del frontend pasan; las 152 pruebas
anteriores del frontend pasan. Siete pruebas nuevas de SMTP pasan localmente:
verifican exigencia de TLS, clasificación de fallos y deadline del supervisor con
transporte simulado. No prueban entrega real. La primera suite conjunta detectó
fallos de acceso y teardown de fixtures tras nuevos requisitos MFA/auditoría; se
interrumpió para aislarlos. Continúa la corrección y regresión.

Logs locales: `/tmp/carrillo-closure-ci.log`,
`/tmp/carrillo-closure-targeted.log`, `/tmp/carrillo-closure-transport.log`.
No afirmar que la suite completa pasa. No desplegar hasta corregir regresiones,
validar restauración y preparar copia del despliegue anterior.

Pendientes externos: archivo privado de API key, remitente y autorización de
correo de prueba solicitados al usuario, certificados/renovación HTTPS y
aceptación del recorrido real en teléfono. Nunca recibir secretos por chat.

### Evidencia adicional de la ejecución continua

- `python3 -m scripts.validate_identity`: 176 pruebas pasan (antes de integrar
  logout MFA en la misma transacción); incluye prueba real de reautenticación
  por contraseña y TOTP sin ampliar el vencimiento absoluto.
- Regresión HTTP adaptada a TOTP real: 48 pruebas pasan; cubre rollback de
  logout con cookies mezcladas y pendientes exactos. No hay bypass productivo.
- `python3 -m scripts.restore_drill`: pasa copia/pg_restore/quarantine aislados.
  Demuestra restauración de códigos usados después de la copia, descifrado de
  semilla con clave externa e invalidación del conjunto mediante cuarentena.
  No constituye recuperación real de Contabo ni aceptación manual.
- `npm --prefix frontend test -- --run tests/identity-flows.test.tsx`: 3 pasan.
- `python3 -m unittest scripts.tests.test_setup_smtp`: 2 pasan.
- `python3 -m scripts.security`: secretos e historial sin hallazgos, locks Python
  sin hallazgos; npm conserva los cuatro hallazgos bajo SEC-2026-001, sin ampliar
  su vencimiento del 31 de octubre de 2026.
- Dos mensajes reales de transporte SMTP fueron aceptados por Resend, únicamente
  al destinatario autorizado. No contenían enlaces operativos ni cambiaban cuentas.
  Pendiente confirmación de llegada y prueba de los recorridos completos por HTTPS.

SMTP configurado mediante `python3 -m scripts.setup_smtp`; claves/configuración
privadas permanecen fuera del repositorio. Nunca registrar su contenido.
Regresión completa de backend en curso mediante
`python3 -m scripts.validate_identity --full-backend`.

### Punto de continuación

El usuario confirmó que recibió ambos mensajes de transporte. No enviar a otros
correos; la autorización está limitada a su destinatario personal indicado.

Validación enfocada más reciente: **178 pasan en 30,27 s**, con el logout MFA
transaccional ya integrado, consumo ciego en cola y confirmación general de buzón
ligada a la sesión que solicitó el mensaje. La regresión HTTP anterior adaptada
completó **48 casos** (incluye tres de reset y siete de transporte compartidos;
no sumar resultados como si fueran casos distintos).

La regresión completa anterior avanzó **270 casos** y encontró una expectativa
histórica de conservar la sesión del usuario afectado en otra empresa. Se actualizó
para exigir revocación global y se relanzó. Aún no declarar suite completa aprobada.
Siguiente trabajo: resolver expectativas de administración/último administrador
con factor confirmado; adaptar las demás fixtures HTTP a MFA real, conservar
las comprobaciones de rechazo y rollback; ejecutar CI/navegador completos.

La ejecución unitaria directa en `.venv` no progresó y fue detenida; no cuenta
como validación aprobada. Usar la imagen Docker fijada para la regresión restante.
El entorno de desarrollo y Contabo siguen sin actualización ni migraciones nuevas.

### Regresión adicional y operación

- `python3 -m scripts.validate_identity --unit-only --maxfail 10`: **336 pasan**.
- Administración/alta HTTP, tres archivos seleccionados mediante `--test-path`:
  **190 pasan** con MFA real y último administrador respaldado por factor confirmado.
- Reautenticación reciente administrativa: **19 pasan** con contraseña/TOTP,
  vencimiento exacto, cambio de contraseña y espera real por lock de PostgreSQL.
- `python3 -m scripts.validate_identity --test-path tests/integration/test_identity_delivery.py`:
  **5 pasan**; ambiguos sin reenvío, tres intentos positivos NOT_SENT y cambio de
  dirección entre claim y envío cancela tanto envío como consumo del token.
- `python3 -m scripts.validate_identity --test-path tests/integration/test_identity_rewrap.py`:
  **2 pasan**, preservación de contador/códigos y rollback junto con auditoría.
- `python3 -m unittest discover -s scripts/tests`: **38 pasan**.
- `npm --prefix frontend test`: **155 pasan**.
- `python3 -m scripts.validate_tls_proxy`: sintaxis Nginx TLS y lectura de archivos
  por UID 101 pasan con certificados temporales; no certifica cadena pública.

La última regresión completa alcanzó **1296 casos aprobados y dos fallidos**;
ambos eran errores de fixture (import de PendingMfaLogin y created_at de evento
histórico). Se están corrigiendo/revalidando; todavía no publicar suite verde.

El usuario creó `panel.taller-carrillo.com` como CNAME al dominio raíz. Ambos
resuelven desde Contabo. La consulta local no resolvió y no se considera evidencia
externa de propagación global. Inventario remoto adicional: sin certbot encontrado,
80/443 libres; PostgreSQL anterior en volumen `taller-carrillo_postgres_data` y
contenedores frontend/backend sin mounts. Conservar su filesystem al respaldar;
no retirar imágenes/volúmenes ni rutas de imágenes del usuario sin inventariar.
NTP remoto confirmado (`NTPSynchronized=yes`). Sin mutaciones remotas todavía.

### Confirmación DNS y recorrido en navegador

La resolución consultada desde Contabo coincide entre panel, raíz y servidor
SSH. El usuario confirmó el CNAME; no se requiere repetir el cambio DNS.

La ejecución de `python3 -m scripts.ci` completó **1300 pruebas backend** y
**155 frontend**. Después fallaron dos de siete comprobaciones del proxy:
la fixture intentaba envejecer solo la contraseña y describía un login imposible
fuera del límite de cinco minutos. Se corrigió para envejecer conjuntamente las
fechas del login sintético y su sesión, conservando las restricciones SQL.
Se está repitiendo CI; no declarar todavía el conjunto completo aprobado.

`python3 -m scripts.browser_qa`: prueba manual en stack efímero separado, factor
cifrado sintético y consumo real del TOTP desde el formulario. Se verificó:
contraseña, petición TOTP previa a selección, selección entre dos membresías
autorizadas, panel de seguridad con sesiones/eventos propios, cierre global y
retorno al formulario de acceso. Sin errores/warnings de consola observados.
No se probó todavía activación por QR, recuperación completa ni móvil/HTTPS.
La captura local `/tmp/carrillo-security-browser.png` muestra el panel previo al
cierre. El navegador rellenó un identificador sintético en el campo TOTP;
revisar comportamiento de autocompletado y presentación antes de aceptación UI.
Credenciales efímeras fuera de stdout; copia temporal privada eliminada al
finalizar. El entorno se cerró mediante la señal explícita de la fixture.

### Resultado conjunto confirmado

La repetición final de `python3 -m scripts.ci` terminó con código 0:
**1300 pruebas backend**, **155 frontend** y **7 pruebas del proxy** aprobadas,
además de lint, formato, tipos, builds, workflow, revisión de secretos/locks y
smoke HTTP. Log: `/tmp/carrillo-closure-ci-final.log`.
`git diff --check` y Ruff local completo también pasan.
La excepción npm SEC-2026-001 sigue vigente sin ampliación; no afirmar cero
vulnerabilidades. La fixture manual de navegador terminó y limpió su stack.

Este resultado sustituye los fallos parciales de CI descritos arriba, sin cerrar
los criterios externos. Pendientes: presentación/autocompletado de interfaz,
QR y recuperación en navegador, imágenes/SBOM según plan, respaldo remoto
recuperable, imágenes de release, HTTPS/renovación y aceptación real en teléfono.
No se han aplicado migraciones a desarrollo ni a Contabo. El inventario aún no
se inicia; la prioridad del usuario sigue siendo cerrar estos criterios de acceso.

### Endurecimiento de imágenes y repetición conservada

Se integró escaneo de las cuatro imágenes y SBOM; detalle en
[entrega 2.39](deliveries/access-2.39.md) y [control de imágenes](container-security.md).
Las imágenes iniciales tenían 142 hallazgos HIGH/CRITICAL sumados entre paquetes;
no son fotografías del servidor. Se redujeron dependencias y aplicaron parches,
sin introducir excepciones de runtime.

Los procesos anteriores dejaron de estar activos y sus logs de /tmp ya no estaban
disponibles al reanudar. No se reutilizó esa ejecución como evidencia verde. La
nueva ejecución bajo umask 077 descubrió un fallo de lectura de secrets por UID
no privilegiado. Se corrigió la provisión, se añadieron regresiones de privacidad/
no sobrescritura y se repitió CI completo. Resultado: **1300 backend, 156 frontend,
7 proxy y 46 scripts**, builds/tipos/lint/smoke y **cuatro imágenes sin hallazgos**.
Logs conservados dentro de `.local/validation/` y SBOM/reportes privados en
`.local/image-reports/190edf7cfcb548a982f9e1e4842df587/`. Restauración completa y
validación TLS temporal pasan con las nuevas imágenes.

Inspección SSH adicional confirma los tres contenedores antiguos activos y
aproximadamente 75,3 GiB libres. El usuario reiteró que se retiren los proyectos
anteriores, conservando el nuevo y la infraestructura necesaria, después del
respaldo recuperable y comprobación del despliegue. No hubo mutaciones remotas.
Sigue respaldo/release/TLS y aceptación real; no declarar el objetivo completo.
