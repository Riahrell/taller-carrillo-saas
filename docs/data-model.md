# Modelo inicial y límites transaccionales

Este documento distingue el modelo objetivo y los contratos implementados por
entrega. Acceso 2.1 incorporó `tenants`, `users` y `tenant_memberships` mediante
`0002_access`; las ampliaciones hasta 2.14 se detallan al final. La interfaz 2.15
reutiliza ese esquema y contrato, sin tablas ni migraciones nuevas. Acceso 2.16
agrega confirmación opcional de sesión mediante 0008, detallada al final.
Los atributos comerciales
se limitan a los requeridos por el plan y a las decisiones de `decisions.md`.
La especificación de cada tarea cerrará contratos antes de generar código.

Confirmación interna 2.16: `sessions.reauthenticated_at` y
`reauthentication_fingerprint` nulos hasta verificar explícitamente contraseña y
rotar token en un commit. No hay backfill. CHECK exige coherencia de ambas
columnas y vida absoluta. Desde 2.18, la guardia de cinco minutos se exige dentro
de las transacciones de alta/cambio administrativos; alta la comprueba de nuevo
antes del commit. 2.19 agrega tooling de seguridad sin modificar ese esquema.
Contrato y downgrade con revocación: `reauthentication-contract.md`.

2.20 propone el modelo lógico MFA/recuperación en `mfa-recovery-contract.md`:
estado de seguridad global/revisión, factor cifrado, pendientes tipados, códigos
offline, evidencia de sesión/correo y eventos globales separados de auditoría
comercial. 2.21 implementa primitivas. **2.22 agrega 0009**, solo tablas de
credenciales cifradas, conjuntos y hashes offline; sin backfill o MFA en login.
2.23 agrega 0010: eventos globales tipados de consumo MFA, append-only para
la app. 2.24 añade 0011, cuotas MFA por UUID de usuario/IP, independientes de
contraseña; ver `mfa-verification-contract.md`. El resto de auditoría y modelo
lógico sigue pendiente. Límites físicos en
`mfa-persistence-contract.md`; no reutilizar eventos comerciales con actor/tenant
falsos para auditoría global.

## Entidades

| Grupo | Entidades y relaciones |
| --- | --- |
| Acceso | `users`, `tenants`, `tenant_memberships`, `sessions`; usuario global, membresía por empresa y sesión revocable. |
| Catálogo | `categories`, `products`, `workshop_services`; producto con SKU opcional único por empresa, precio y estado activo. |
| Inventario | `stock_movements`, `stock_balances`; movimiento con producto, cantidad, valoración, origen, autor y fecha; proyección única por tenant/producto. |
| Compras | `suppliers`, `purchases`, `purchase_items`; recepción completa enlazada a movimientos, una vez. Pago de compra separado de la recepción. |
| Clientes | `customers`, `vehicles`; orden conserva sus relaciones históricas aunque cambie el propietario actual. |
| Taller | `work_orders`, `work_order_items`, `work_order_status_history`, `work_order_attachments`; consumos vinculados al detalle de repuesto. |
| Ventas | `sales`, `sale_items`, `payments`; detalle conserva nombre, precio y costo aplicado. Origen directo o vinculado a orden. |
| Correcciones | Registros de anulación y compensación enlazados al original, sin borrado; diseño físico detallado en hito 5. |
| Caja | `cash_sessions`, `cash_movements`, `expenses`; pagos de compras y gastos con origen verificable, sin duplicar efectos. |
| Trazabilidad | `audit_events`, registro técnico de idempotencia con empresa, operación, clave, huella y resultado. |

No crear tablas de reportes por defecto: son consultas sobre fuentes identificadas.
No añadir entidades de crédito, nómina, lotes, almacenes o contabilidad formal.

## Restricciones

- IDs técnicos UUID. Timestamps con zona horaria en UTC.
- Todas las entidades de negocio incluyen tenant o una derivación inequívoca.
  Preferir tenant explícito y FK compuesta `(tenant_id, id)` en referencias
  entre entidades de negocio para impedir cruces incluso fuera del router.
- Membresía única `(tenant_id, user_id)`, SKU no vacío único por tenant cuando
  exista, saldo único `(tenant_id, product_id)`.
- Una sesión de caja abierta por tenant mediante restricción concurrentemente
  segura. Una venta activa por orden, conservando las ventas anuladas.
- Líneas de producto o servicio identifican exactamente una clase de referencia.
- CHECK para cantidades, importes y estados según su semántica; no aplicar
  indiscriminadamente importe positivo a compensaciones o diferencias.
- Sin cascadas que borren ventas, pagos, movimientos ni auditoría.
- Catálogo desactivable, relaciones históricas preservadas.
- Relaciones de origen de stock, pagos y caja deben ser verificables. No usar
  únicamente un texto libre como sustituto de integridad referencial.

## Unidades de trabajo

1. Recepción: compra validada y bloqueada -> movimientos de entrada -> saldo y
   valoración -> recepción confirmada -> auditoría e idempotencia -> commit.
2. Consumo: orden y autorización -> detalle -> saldo bloqueado -> salida y
   valoración -> vínculo al consumo -> auditoría e idempotencia -> commit.
3. Venta directa: autorización/caja -> precios y disponibilidad -> líneas y pagos
   -> salidas y caja -> auditoría e idempotencia -> commit.
4. Venta de orden: comprobar consumos previos -> venta y pagos -> caja ->
   auditoría e idempotencia -> commit; no repetir las salidas de repuestos.
5. Cierre: bloquear sesión contra cobros concurrentes -> calcular fuentes ->
   contado/diferencia/motivo -> cierre y auditoría -> commit.
6. Anulación: conservar original -> registrar reembolso y su estado real ->
   compensaciones autorizadas, con stock solo ante retorno físico -> auditoría.
   No mantener transacciones abiertas mientras se realiza un reembolso externo.

Cada implementación definirá su orden global de bloqueos y probará conflictos
entre cobro, consumo, cierre y anulación, no solo entre dos ventas idénticas.

## Fuentes para reportes

- Ventas: ventas confirmadas y compensaciones financieras, sin borrar originales.
- Cobros: pagos efectivos y reembolsos confirmados, separados por método.
- Efectivo: apertura y movimientos de caja; pagos no efectivos no lo alteran.
- Existencias y valoración: movimientos reconciliados con saldos materializados.
- Costos: salidas valoradas y retornos físicos compensatorios; no sumar de nuevo
  compras ni duplicar consumo de orden con su snapshot de costo en venta.
- Gastos: gastos registrados; movimiento de pago enlazado no es un segundo gasto.
- Compras: adquisición y pagos consultables separadamente.
- Cualquier indicador de utilidad requiere política explícita de reconocimiento,
  costos e impuestos antes de etiquetarlo como tal.

## Contrato implementado: acceso 2.1

- Los tres modelos tienen `id` UUID generado en servidor, `created_at` con zona
  horaria y valor UTC de DB, e `is_active` obligatorio con valor inicial true.
- `tenants`: `name` obligatorio (hasta 200 caracteres), no vacío.
- `users`: `email` global único (hasta 254), normalizado con strip/lower por el ORM
  y comprobado con lower/trim por DB; `password_hash` obligatorio (hasta 512), no
  vacío y sin valor de reserva. Acceso 2.2 incorpora validación del correo
  y generación/verificación Argon2id
  desde el bootstrap CLI. El CHECK de DB no verifica el formato criptográfico;
  los futuros casos de uso deben utilizar estas utilidades.
- `tenant_memberships`: FK restrictivas a tenant y usuario, `role` obligatorio
  sin valor por defecto, CHECK con admin/cashier/mechanic/read_only y pares únicos
  `(tenant_id, user_id)` y `(tenant_id, id)`. El segundo permite futuras FK
  compuestas hacia membresías. Se reutiliza la membresía al reactivarla.
- La pertenencia a otra empresa no concede acceso a la seleccionada. El resolvedor
  interno consulta los tres estados activos y vuelve a leer el rol almacenado.
  Su resultado no concede permisos y no debe reutilizarse entre peticiones.
- Esta fase no crea sesiones, configuración monetaria/zona del tenant, auditoría,
  permisos de módulos ni APIs. Las decisiones USD y America/El_Salvador siguen
  vigentes para cuando se implementen sus casos de uso.
- No hay RLS. El rol DB de aplicación tiene DML sobre estas tablas; el aislamiento
  probado corresponde al resolvedor interno, no a consultas SQL arbitrarias.
  La futura capa HTTP necesitará identidad y tenant desde sesión verificada.
- No se permiten borrados de empresas/usuarios referenciados por membresías.
  La protección del último administrador y la revocación de sesiones requieren
  servicios transaccionales posteriores; no están resueltas por estas FK.

## Contrato implementado: acceso 2.3

`sessions` añade id UUID, tenant_id, membership_id, token_hash único de 64 hex
minúsculas, created_at, last_seen_at, expires_at y revoked_at opcional. No incluye
contraseña, token en claro, rol elegido por cliente ni IP/agent de navegador.
Todas las fechas llevan zona horaria. El servicio asigna fechas desde DB; no hay
valores de creación automáticos que puedan construir una sesión a medias.

FK `(tenant_id, membership_id)` a `(tenant_id, id)` de membresías, con RESTRICT.
Índice por membership_id para revocación y unique de token_hash para resolución.
CHECK: expires_at = created_at + 8h, actividad en [creación, expiración), revocación
no anterior a creación y digest SHA-256 con formato canónico. El CHECK de formato
no demuestra aleatoriedad; el servicio es quien genera el token seguro.

La vigencia se comprueba en cada resolución y la actividad se escribe dentro de
la transacción del llamador. Caducar no borra registros; limpieza/retención y
sesiones simultáneas máximas no se definen en esta fase. No hay uso desde HTTP.
Los cambios de contraseña/privilegios deberán utilizar las funciones de revocación,
ya probadas con el cambio y rollback en una única transacción; no hay aún APIs
para esos cambios ni protección del último administrador.

## Contrato implementado: acceso 2.4

login_throttles: clave primaria (scope, key_hash), scope account/ip, SHA-256
hexadecimal minúsculo, window_started_at con zona y failures obligatorio.
CHECK de ámbito, formato de clave y rango 0–5 para cuenta/0–30 para IP. Índice de
window_started_at para mantenimiento futuro. Sin FK a usuarios: también protege
correos inexistentes sin crear identidades ni filtrar si existen. Estado global,
sin tenant, correo ni IP en claro. No representa anonimización criptográfica.

La ventana se renueva al vencer y sus contadores se actualizan bajo bloqueo; no
hay tarea de limpieza implementada todavía. El servicio de autenticación confirma
tanto sus rechazos como los éxitos antes de devolver el resultado al transporte.

## Contrato implementado: acceso 2.6

`login_challenges`: id UUID; user_id con FK restrictiva e índice; token_hash
único, binding_hash y password_fingerprint, los tres SHA-256 hex minúsculos;
created_at, expires_at indexado y finished_at opcional, todos con zona horaria.
No hay valores por defecto que acrediten identidad ni contraseña/token/cookie
en claro. La huella corresponde al hash Argon2 almacenado, no a la contraseña.
No es anonimización y debe protegerse como material de seguridad.

CHECK de formato de las tres huellas, vida exacta de cinco minutos y finished_at
no anterior a creación. finished_at significa consumido o cancelado, incluso
después de caducar. El índice de expiración prepara la limpieza, aún no implementada.
El servicio, no el CHECK, garantiza aleatoriedad, procedencia y verificaciones.

El pendiente acredita solo contraseña verificada de un usuario global. No lleva
tenant/rol ni concede acceso comercial. Las opciones son IDs de membresías y nombres
consultados tras autenticar; la selección vuelve a comprobar pertenencia y estados
actuales. No crea sesión hasta confirmar una opción válida. Sin snapshots de permisos.
Un nuevo login correcto cierra pendientes anteriores bajo lock del usuario; las
sesiones ya emitidas tienen su ciclo de revocación independiente.

Orden de bloqueo global: IP -> cuenta -> usuario -> pendientes por UUID -> sesiones.
La selección/revocación no adquiere buckets de cuenta/IP. El lock del usuario se
mantiene desde verificación hasta commit y serializa nuevas selecciones, consumo,
revocación y cambios coordinados de contraseña. Consumo y emisión son una única
transacción; resultado/token no sale si falla commit. No hay RLS ni protección
contra SQL arbitrario del rol de aplicación.

## Transporte de acceso 2.7

Sin migración nueva: sesiones, contadores y pendientes usan el esquema existente.
El adaptador HTTP confirma transacciones antes de emitir/retirar cookies o devolver
contexto. Logout coordina revocación de sesión y pendiente en una transacción,
prebloqueando usuarios por UUID si las cookies remiten a identidades distintas.
Los tokens de navegador no se guardan en claro ni se serializan en las respuestas.
El contexto HTTP relee nombre de empresa, membresía y rol después de comprobar
la sesión; no añade autoridad del cliente ni snapshots de permisos.

## Administración de acceso 2.9

`0006_membership_audit` añade `audit_events` para cambios de rol/estado de
membresías. Contrato inicial tipado, no un contenedor genérico de payloads:

| Campo | Función |
| --- | --- |
| id | UUID del evento, generado por servidor. |
| tenant_id | Empresa de la sesión autorizada. Índice de consulta por empresa. |
| actor_membership_id | Autor, FK compuesta con tenant_id a membresías. |
| target_membership_id | Membresía modificada, FK compuesta con tenant_id. |
| action | Únicamente membership_changed en esta revisión. |
| before_role / after_role | Roles permitidos, sin texto libre. |
| before_active / after_active | Estado anterior y posterior explícitos. |
| created_at | Fecha UTC con zona, asignada mediante reloj DB tras los locks. |

CHECK exige un cambio real y roles/acción válidos. Las FK usan RESTRICT, sin
borrado en cascada; incluso al modificar al propio autor se conserva su membresía.
No guarda tokens, contraseñas, correo ni IP. El rol carrillo_app recibe solo
SELECT/INSERT en esta tabla: la migración revoca UPDATE/DELETE heredados de los
permisos iniciales. El owner conserva administración, incluida limpieza de tests.
No se promete resistencia a un administrador DB ni integridad ante INSERT manual
del runtime. No hay RLS ni endpoint de lectura de auditoría en esta entrega.

Cambio, revocación de todas las sesiones objetivo, cancelación de pendientes
globales y evento se confirman juntos. No-op no genera evento ni revoca. El servicio
no cambia IDs, empresa, usuario global ni otras membresías. Reactivar reutiliza
la membresía única y no restablece sesiones antiguas. Empresa -> usuarios por UUID
-> hijos es la jerarquía del servicio; el lock de empresa serializa el conteo del
último administrador. Las futuras altas/cambios globales deben coordinarse igual.

## Transporte de administración 2.10

Sin migración nueva ni columna de versión: el esquema sigue en
`0006_membership_audit`. GET/PATCH exponen solo membership_id, role e is_active
a administradores de la empresa actual. El ETag deriva de esa representación,
no se almacena ni contiene secretos. Un If-Match diferente al estado releído bajo
bloqueo rechaza la escritura antes de cambiar membresía/revocar/auditar.
El mismo estado produce el mismo tag, incluso si hubo cambios intermedios que
lo restituyeron: no representa una secuencia histórica ni idempotencia general.
Lectura y resultado de cambio salen solo después de commit. La lectura comprueba
sesión/permiso y actualiza actividad dentro de su propia transacción breve.

## Listado de administración 2.11

Sin cambio de esquema: consulta tenant_memberships de la empresa de sesión y
une users para correo/estado ya existentes. La respuesta incluye membership_id,
email, role, is_active y user_is_active; no nueva columna, token, hash ni ID global.
Paginación por índice (tenant_id, id), frontera UUID exclusiva y máximo 101 filas
consultadas para devolver hasta 100. Sin contador global ni persistencia del cursor.
Lectura viva entre páginas; no es snapshot de toda la empresa ni fuente para
exportación histórica. No crea eventos ni cambia membresías al consultar.

## Contexto de administración 2.12

Sin migración ni columna nueva. Los DTO internos de lectura/resultado transportan
actor_membership_id desde la autorización hasta las cabeceras HTTP tras commit.
Se excluye del JSON y del ETag. X-Expected-Membership es una precondición opcional
del cliente comparada contra el actor real, sin persistencia ni selección de
identidad. La pantalla conserva solo una página y un borrador en memoria, y
reutiliza el cambio transaccional existente para rol/estado, auditoría y revocación.

## Alta interna 2.13

`0007_membership_creation_audit` amplía CHECKs y permite before_role/before_active
null exclusivamente en eventos `membership_created`; after_role sigue siendo un
perfil válido y after_active debe ser true. Los eventos `membership_changed`
siguen exigiendo antes no nulo y estado distinto. No se altera evidencia anterior,
FKs de empresa/actor/objetivo ni privilegios SELECT/INSERT de aplicación.
Downgrade a 0006 aborta si existen eventos de alta: no se destruye evidencia ni
se fabrica un estado anterior para hacerla encajar en ese esquema.

El servicio crea users/tenant_memberships/evento en un commit. Cuenta y membresía
activas, rol objetivo explícito y tenant del actor autorizado. Unicidad global de
users.email arbitra colisiones entre empresas, sin adjuntar ni actualizar al
usuario existente. El resultado solo expone ID de membresía/rol/estado; sin ID
global, hash o contraseña. No emite sesiones ni crea empresas nuevas.

## Transporte de alta 2.14

Sin migración nueva: POST administrativo usa el servicio 2.13 y la auditoría 0007.
Entrada email/password/role; empresa, actor y estados activos se resuelven en
servidor. X-Expected-Membership obligatorio es una comparación del contexto, sin
persistencia ni selección de identidad. Respuesta 201 mínima después de commit,
sin modificar las identidades globales existentes ni emitir sesión al nuevo usuario.
Duplicados siguen arbitrados por unicidad global de correo, sin crear membresía
en otra empresa por conocer el email. No se agregan campos de invitación o recuperación.

2.25 agrega 0012: revisión global/obligación MFA en users y eventos de avance,
sin correo verificado o integración de login. Ver `deliveries/access-2.25.md`.

Acceso 2.26: `mfa_pendings` guarda hashes separados de token/navegador, propósito
login_totp/enrollment, usuario/revisión/huella de contraseña y tiempos absolutos
de 5/15 minutos. Solo estado restringido; sin empresa o prueba MFA/buzón.
Migración 0013, sin filas operativas iniciales. Ver `deliveries/access-2.26.md`.

Acceso 2.27 (0014): mfa_pendings admite login_verified con credential_id del
mismo usuario y factor_verified_at entre contraseña y creación. Los otros
propósitos exigen ambos NULL. Login conserva cinco minutos desde contraseña;
no hay backfill de prueba. Ver `deliveries/access-2.27.md`.

Acceso 2.28 (0015): sesiones guardan revisión y referencia única opcional a
mfa_pendings. Esa referencia conserva evidencia global, no un MFA flag del
cliente. Cookies antiguas con revisión NULL se rechazan sin backfill. Retención
debe respetar FK de sesión a prueba. Ver `deliveries/access-2.28.md`.

Acceso 2.29 reutiliza 0015, sin migración o backfill. El inicio MFA interno puede
establecer obligación/revisión, revocar sesiones/pendientes globales y registrar
evento antes de crear el nuevo pendiente en el mismo commit. Reiniciar desde
el mismo navegador cierra sus pendientes previos; no borra la cuota MFA.
