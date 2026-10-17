# Revisión técnica del Plan Maestro v1.1

Fecha: 2026-10-16, America/El_Salvador.
Alcance: revisión y propuestas de mejora, sin implementación ni cambios remotos.

El usuario entrega el nuevo plan para revisión y aclara que es una noción con
libertad para opinar y mejorarlo. Se interpreta como base de requisitos y diseño,
no como orden de ejecutar los prompts o comandos incluidos en el documento.
La autorización previa para limpiar la VPS si hace falta permanece en contexto;
los anexos no describen el estado real del servidor ni constituyen un procedimiento
ya validado para él.

## Fuentes y estado comprobado

- v1.1: `/home/riahrell/Descargas/Plan_Maestro_Taller_Carrillo_IA_v1.1.pdf`,
  28 páginas leídas completas. SHA-256:
  `6478297c5a35fce31d9592f579217f83e4e567a9b4a095df25c14cb2566eaec7`.
- v1.0: `/home/riahrell/Descargas/Plan_Maestro_Taller_Carrillo_IA.pdf`, 13 páginas,
  comparadas con el nuevo alcance. SHA-256:
  `5029d6af088c55fc99b310a1181d7cdbdfc692fac21c054c2582f6512c0ff5e6`.
- Texto extraído completo; inspección visual de páginas 12, 15 y 27 para
  contratos públicos, notificaciones y ejemplos de infraestructura.
- Base de código revisada: commit `dca9170`, acceso 2.17. Servicio y API de
  reautenticación disponibles; formulario y guardia obligatoria en transacciones
  administrativas pendientes. Sin módulos comerciales o sitio público aún.
- Las 864 pruebas corresponden a la entrega 2.17 ejecutada anteriormente;
  no se repitieron durante esta revisión ni demuestran los nuevos requisitos.
- CI actual tiene lint, tipos, pruebas y builds. No integra todavía gitleaks,
  pip-audit, npm audit, Trivy o actualización automatizada. No existe evidencia
  desde aquí de TLS, hardening o restauración completa en el VPS.

## Dictamen

Recomiendo evolucionar el proyecto con v1.1 y las precisiones siguientes.
La base existente es compatible; no reiniciar la aplicación o reescribir acceso.
Se mantiene monolito modular, PostgreSQL, FastAPI, Next.js y Compose. El worker
usa el mismo código/imagen backend en otro proceso: no es un microservicio ni
justifica Redis, una cola externa o nuevas capas genéricas.

Los cambios sustanciales frente a v1.0 son vitrina pública sin compra en línea,
captación/seguimiento de interesados, correo transaccional y outbox; además,
2FA administrativo y seguridad de infraestructura/CI como requisitos explícitos.
Taller pasa a hito 5; cobro a 6; reportes a 7 y producción a 8. Conservar los
números y resultados de entregas históricas; actualizar la planificación futura.

## Precisiones que recomiendo antes de implementar lo nuevo

### 1. Separar el sitio público del panel por origen

Preferencia técnica: dominio principal para público y `panel.taller-carrillo.com`
para administración, usando la misma aplicación y backend. Es una propuesta de
URLs, no configuración DNS realizada ni un origen incrustado en código.
Cookies __Host- del panel sin Domain, CORS cerrado y rutas permitidas por host
en proxy/aplicación. Grupos de rutas Next.js separan layouts pero por sí solos
no restringen el acceso por host. Noindex no es control de acceso.

El dominio público solo sirve la API pública; no publicar las rutas administrativas
en él. El formulario público necesita su propia política de origen y antiabuso,
sin desactivar el middleware CSRF de administración. Next SSR tendrá acceso
solo a la red/API necesaria, nunca a la red ni credenciales de PostgreSQL.
El backend requiere salida limitada para validar Turnstile; worker, para envíos.

### 2. Resolver CSP con nonce frente a ISR

Las secciones 9.5 y 13 combinan ISR/estático y CSP con nonce. La documentación
oficial de Next.js indica que un nonce por petición exige renderizado dinámico
y desactiva ISR/optimización estática para esas páginas.

Por la prioridad de seguridad, recomiendo SSR con nonce al principio, sin caché
compartida del HTML; optimizar assets/imágenes y datos públicos seguros. Medir
LCP y carga real antes de adoptar otra estrategia. No recurrir silenciosamente
a unsafe-inline ni basar producción en SRI experimental para conservar ISR.
Si posteriormente se elige generación estática, revisar y probar una política
CSP compatible en una tarea específica.

[Referencia Next.js: CSP y renderizado](https://nextjs.org/docs/app/guides/content-security-policy).

### 3. Precisar retirada de publicaciones y disponibilidad

ISR de 1–5 minutos puede conservar una ficha después de despublicarla, mientras
el plan exige 404 para productos ocultos/inactivos. Para el MVP propuesto sin
caché HTML, verificar publicación/actividad actual antes de responder; cachés
de datos nunca sustituyen esa decisión. Probar detalle, listas, metadatos y
sitemap. Si se incorpora CDN/ISR, definir invalidación y latencia de retirada
antes de afirmar que despublicar es inmediato. No se pueden retirar copias ya
descargadas por visitantes o buscadores.

La disponibilidad se deriva en un servicio privado y se publica solo como etiqueta;
la consulta pública no necesita saldos, costos o movimientos. Usar Consultar
como valor inicial, con precio/etiqueta opcionales administrados explícitamente,
sin cantidades exactas. La política concreta se cierra antes del catálogo público.

### 4. Distinguir el botón de WhatsApp de un aviso automático

El objetivo inicial habla de avisos por correo y WhatsApp, pero 9.8 y 10 proponen
correo + wa.me y dejan Cloud API para después. Recomiendo ese MVP acotado:
el formulario crea lead y aviso por correo; wa.me abre una conversación manual.
Un clic en wa.me no registra por sí mismo un lead ni confirma que se envió un
mensaje. No prometer aviso automático de WhatsApp por cada formulario.
Telegram y Cloud API quedan desactivados hasta decidir su necesidad y contrato;
no automatizar WhatsApp Web. No hace falta contratar esas integraciones ahora.

### 5. Corregir la garantía de duplicados de la outbox

Lead y evento se confirman juntos. Unicidad impide crear dos filas del mismo
evento/canal; SKIP LOCKED permite repartir trabajo entre consumidores. Ninguna
de esas medidas confirma por sí sola entrega externa exactamente una vez.
Ejemplo: el proveedor acepta el correo y el worker muere antes de marcarlo enviado.
Al recuperar trabajo puede repetirse. Esto es una limitación del diseño propuesto,
no un fallo observado en el código, donde todavía no hay worker.

Recomiendo reclamación breve con lease/propietario, envío fuera de transacción
DB, recuperación de trabajos abandonados, backoff y clave de idempotencia estable
en el proveedor cuando esté disponible. Distinguir aceptado por proveedor de
entregado al destinatario. Si no hay idempotencia externa, reconocer el riesgo
residual y tratar respuestas ambiguas; no prometer ausencia absoluta de duplicados.

Separar retry de la misma clave de idempotencia y nueva solicitud deduplicada:
el retry no incrementa el contador de interesados ni genera otro evento.
Definir destinatarios del evento para que un cambio de configuración no cambie
inesperadamente a quién se envía un reintento.

[PostgreSQL: SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE),
[AWS: riesgos de mensajes duplicados en outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).
La referencia AWS explica el patrón; no propone migrar el proyecto a AWS o SQS.

### 6. Separar imágenes públicas de adjuntos privados

No servir todo el almacenamiento por /media. Fotos curadas del catálogo son
públicas; adjuntos de órdenes pueden contener matrículas, datos personales o
documentos y requieren autorización por empresa. Usar ubicaciones y entrega
separadas. Mantener recodificación, límites, eliminación EXIF, nombres generados
y pruebas de acceso. Un nombre UUID no vuelve privado un archivo público.

### 7. Conservar controles más fuertes y completar MFA

No bajar de 15 a 12 el mínimo actual de alta ni quitar CSRF firmado porque
11.1 lo mencione condicionalmente. Mantener __Host-, sesión revocable y límites.
La reautenticación por contraseña no es 2FA: TOTP de admin y recuperación de
un solo uso deben impedir emitir sesión antes del segundo factor, también en
la selección multiempresa. Alta/desactivación de MFA y recuperación requieren
contratos, revocación, protección de secretos y pruebas contra bypass/replay.
El retraso progresivo pedido por v1.1 no equivale a la cuota fija actual: diseñarlo
sin ocupar trabajadores durmiendo. RLS se evalúa; no se declara implementado ni
sustituye filtros/relaciones compuestas por tenant.

### 8. Adaptar ejemplos de despliegue; no copiarlos literalmente

Cloudflare es opcional en el texto y obligatorio en partes del checklist: marcar
esos criterios como condicionales a su adopción. Si se usa, cerrar el origen y
validar la cadena de proxies; no confiar automáticamente en CF-Connecting-IP ni
solo en que el nombre del dominio esté proxificado. La IP publicada anteriormente
no se vuelve secreta por añadir un proxy.

El anexo inicia HSTS con un año, aunque 12.5 pide empezar corto: aplicar aumento
gradual después de verificar HTTPS y recuperación. SSH administrativo restringido
es una excepción explícita al criterio web de solo 80/443. Revisar UFW junto con
las reglas efectivas de Docker y comprobar desde fuera. Caddy es una opción;
el Nginx existente no necesita sustituirse para cumplir el plan.

No ejecutar los anexos, deshabilitar SSH o limpiar la VPS durante esta revisión.
El despliegue necesita inventario, respaldo, reversión y pasos concretos revisables,
conservando la autorización ya expresada por el usuario para limpiar si hace falta.

## Estructura y modelo ampliados propuestos

Mantener acceso actual; agregar módulos solo cuando se implemente su tarea:

```text
backend/app/modules/
  access/                 existente; completar confirmación, MFA y recuperación
  catalog/ inventory/ ... módulos internos previstos
  public/                 routers y schemas exclusivamente públicos
  leads/                  creación, seguimiento, notas y transiciones
  notifications/          outbox, plantillas, proveedores y worker
frontend/src/app/
  (public)/               catálogo, contacto y privacidad
  (auth)/                 acceso y segundo factor
  (dashboard)/panel/      administración con layout/noindex propios
ops/                      proxy, despliegue, respaldo y runbooks
```

El prefijo/ruta final del panel y las restricciones por host se cierran antes
de reorganizar URLs; estos directorios no se han creado como implementación.
Nueva persistencia prevista según el PDF: tenant_settings, catalog_listings,
catalog_images, leads, lead_notes y notification_outbox. tenant_domains solo
si se habilitan varios talleres públicos. Ampliar la auditoría tipada actual;
sus CHECK/permisos no aceptan todavía eventos genéricos de todos los módulos.
MFA requiere su diseño de persistencia antes de migrar.

Mantener FK compuestas por tenant, publicación separada del producto interno,
slug único por empresa, consentimiento versionado, teléfono E.164 y HMAC de IP
con retención definida. HMAC no convierte los datos en anónimos ni autoriza
guardarlos indefinidamente. No inventar ahora columnas, endpoints o migraciones
adicionales a las propuestas: cada tarea cierra su contrato primero.

## Orden de trabajo recomendado y aceptación

Actualización 2026-10-17: 2.18 implementa el primer punto y 2.19 integra escaneos;
evidencia en `../deliveries/access-2.18.md` y `../deliveries/access-2.19.md`.
La revisión anterior describe el estado de 2.17 en que se recibió el PDF.
SEC-2026-001 conserva un aviso dev-only visible hasta su revisión/vencimiento.
2.20 define el contrato MFA/recuperación en `../mfa-recovery-contract.md`.
2.21 implementa solo primitivas internas TOTP/cifrado/códigos offline.
2.22 agrega persistencia/consumo en 0009 y 2.23 evidencia global atómica en
0010; 2.24 añade cuotas compartidas en 0011. Siguiente 2.25: estado global/
revisión de seguridad. Integrar pendientes y todos los
caminos de autenticación antes del inventario; MFA todavía no está activo.

1. Completar 2.18: UI de confirmación y guardia dentro de alta/cambio; impedir
   escritura sin prueba reciente y probar pérdida de contexto/rotación entre pestañas.
2. Cerrar deuda de fundación/acceso antes del inventario: escaneos CI, 2FA admin,
   recuperación/revocación, auditoría, CSP y ensayo de restauración aislada.
   Requisitos de VPS se ejecutan con acceso seguro y runbook, no se presumen hechos.
3. Hito 3: catálogo e inventario con transacciones/concurrencia/reconciliación.
4. Hito 4: publicación controlada, API pública allowlist, imágenes, leads y outbox;
   proveedor simulado primero, envío real autorizado y pruebas de abuso antes
   de abrirlo. Este hito puede adelantarse tras catálogo y seguridad si el negocio
   lo prioriza; no hace falta acabar ventas/finanzas para publicar la vitrina.
5. Hitos 5–8 según nuevo plan: taller, cobro, reportes y preparación productiva.

Agregar aceptación que detecte fallos reales: sesión antes de MFA, dato privado
en contrato público, ficha retirada visible, cache de otro tenant, token Turnstile
inválido/servicio caído, reintentos idempotentes y carrera de deduplicación; muerte
del worker después de aceptación externa; dos workers; adjunto privado público;
backup restaurado y DNS/TLS/firewall comprobados desde fuera. El lead ya aceptado
se conserva si falla correo; si falla anti-bot no se acepta el formulario y se
ofrece contacto manual. No confundir ambas garantías.

No hay decisiones que bloqueen esta revisión o continuar 2.18. Antes de hito 4
se requieren datos reales del taller, remitente/destinatarios, proveedor, política
de retención/consentimiento y alcance de cajero; hasta definirlo, mínimo privilegio
y sin conceder lectura de leads por defecto. URL final/proxy/CDN/objetivos de
recuperación se concretan antes del despliegue. No se aprobaron gastos, compraron
servicios o activaron envíos por documentar estas opciones.

## Resultado de esta tarea

Solo esta revisión y su enlace en README. Sin cambios de aplicación, esquema,
entorno o servidor. PDF fuente intacto. Verificación documental: lectura completa,
comparación, inspección visual y `git diff --check`; no se ejecutó nueva suite ni
se certifica cumplimiento de v1.1. Los controles pendientes siguen pendientes.
