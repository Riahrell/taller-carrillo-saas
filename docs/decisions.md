# Taller Carrillo: decisiones de diseño

Estado: decisiones adoptadas por delegación del usuario el 2026-10-16.
Fuente funcional: Plan Maestro v1.0, leído completo (13 páginas).
Este documento registra el diseño. El estado ejecutado de cada entrega se
documenta por separado en `deliveries/` y en el README del repositorio.

## 1. Arquitectura

- Monorepo, monolito modular FastAPI y frontend Next.js App Router.
- Python, Pydantic v2, SQLAlchemy 2.x, Alembic y PostgreSQL.
- React Hook Form, Zod, TanStack Query y cliente HTTP centralizado.
- Docker Compose con backend, frontend, PostgreSQL y proxy. Sin Redis,
  microservicios, colas ni abstracciones de repositorio preventivas.
- Mismo origen público para interfaz y API; proxy con ruta de API estable.
  CORS cerrado salvo orígenes explícitamente habilitados en desarrollo.
- SQLAlchemy síncrono y una sesión por caso de uso; transacciones breves.
  La espera de archivos o servicios externos no ocurre con bloqueos abiertos.
- Router delgado; servicio coordina autorización, reglas y transacción.
  Servicios subordinados no hacen commits independientes.
- Errores de dominio traducidos a HTTP en un punto común.
- Versiones exactas y archivos de bloqueo se resolverán y verificarán al
  implementar la fundación; este documento no afirma compatibilidad ejecutada.

## 2. Empresas e identidad

- Identidad global por correo normalizado y membresías por empresa.
- Correo: eliminar espacios exteriores y normalizar a minúsculas, con
  unicidad global según esa política. No aplicar reglas particulares de Gmail.
- Una membresía activa por usuario y empresa, con un rol inicial.
- Un usuario puede pertenecer a varias empresas. El contexto activo reside
  en la sesión; cambiarlo exige comprobar una membresía activa en servidor.
- Un identificador enviado para seleccionar empresa no concede autorización.
  Los contratos de negocio no admiten tenant, rol ni autor elegidos por cliente.
- Cambiar de empresa limpia carrito y caché del cliente; las claves de caché
  incluyen contexto de empresa para evitar mostrar datos de otra sesión.
- Verificar usuario y membresía activos en cada petición autenticada.
- Primer administrador mediante comando controlado y transaccional; sin
  endpoint público de registro de empresa o asignación de administrador.
- UUID para identificadores técnicos. No sustituyen las verificaciones de acceso.

## 3. Sesión y seguridad

- Sesión opaca revocable en PostgreSQL; almacenar hash del token de sesión.
- Cookie HttpOnly, Secure en producción y SameSite=Lax; sin credenciales
  persistentes en localStorage. HTTPS obligatorio en producción.
- Protección CSRF para operaciones con efectos, comprobación de origen y
  política de cookies consistente. SameSite no sustituye CSRF.
- Caducidad: 30 minutos de inactividad y máximo absoluto de 8 horas.
- Logout revoca sesión; cambio de contraseña y cambios de privilegios
  revocan las sesiones afectadas. No permitir eliminar el último administrador
  activo de una empresa mediante una operación ordinaria.
- Contraseñas con Argon2id, sin credenciales ni secretos predeterminados.
- Login con errores genéricos y límites por cuenta e IP; estado compartido
  en PostgreSQL y protección del proxy, sin dependencia de memoria local
  de un único proceso. Parámetros iniciales: 5 fallos por cuenta en 15 minutos
  y 30 por IP en 15 minutos; sin bloqueo permanente de cuenta.
- Configuración validada al iniciar, secretos externos y logs sin credenciales,
  cookies, tokens ni URL de conexión. Documentación interactiva deshabilitada
  en producción por defecto. Solo el proxy publica puertos de entrada.

## 4. Moneda, cantidades y tiempo

- Moneda inicial de Taller Carrillo: USD. Una moneda por empresa; sin
  conversiones. No cambiarla después de registrar operaciones financieras.
- Zona inicial: America/El_Salvador, configurable por empresa.
- Dinero final: NUMERIC(20,2); cantidades: NUMERIC(18,3); costo unitario y
  valoración interna: NUMERIC(28,6). Decimal en Python; nunca float.
- Admitir cantidades fraccionarias de hasta tres decimales; rechazar mayor
  precisión en entradas en lugar de redondear silenciosamente cantidades.
- ROUND_HALF_UP explícito. El importe monetario de una línea se redondea a
  dos decimales; el total es suma de líneas monetarias. Documentar y probar
  por separado cualquier ajuste requerido por la política fiscal validada.
- Los costos conservan seis decimales hasta su presentación monetaria.
- Timestamps con zona horaria, persistidos en UTC. Reportes por intervalos
  semiabiertos [inicio, fin) derivados de fechas locales.

## 5. Inventario y compras

- Una existencia agregada por empresa y producto en el MVP; sin almacenes,
  lotes, series ni reservas. Servicios no afectan existencias.
- Libro de movimientos como fuente de verdad; saldo materializado de cantidad
  y valoración actualizado en la misma transacción.
- Stock negativo prohibido. Todas las operaciones pasan por el servicio común.
- Bloquear saldos por empresa/producto en orden estable. Resolver también la
  creación concurrente del primer saldo mediante unicidad y estrategia probada.
- Costeo promedio ponderado móvil al recibir existencias. Cada salida conserva
  el costo aplicado; compras posteriores no reescriben costos históricos.
- Valoraciones internas con seis decimales. Al retirar toda la existencia,
  retirar su valoración restante para evitar residuos con cantidad cero.
- Ajuste por conteo físico final no negativo: registrar conteo y diferencia,
  motivo y autor. La cantidad anterior se obtiene bajo bloqueo en servidor.
  Un conteo sin diferencia conserva su evidencia sin inventar una entrada/salida.
- Si un ajuste positivo carece de costo histórico utilizable, el administrador
  debe declarar su costo autorizado; no asignar cero silenciosamente.
- Recepción completa de compra en el MVP, una sola vez. Sin recepciones
  parciales, costos adicionales distribuidos ni cuentas por pagar en esta etapa.
- Recepción aumenta inventario; no implica por sí misma un pago ni un gasto.
  Pagos de compra registrados se vinculan a la compra, sin duplicar recepción.
- Producto/servicio inactivo no puede añadirse a nuevas operaciones; referencias
  ya confirmadas y saldos históricos permanecen consultables.

## 6. Órdenes de trabajo

- Cliente, vehículo y técnico pertenecen a la empresa de la orden.
- Secuencia normal: recepción -> diagnóstico -> aprobación -> reparación ->
  listo -> entrega. El estado aprobación significa aprobación registrada;
  sin ella no se inicia reparación. Sin saltos ni retrocesos en el MVP.
- El mecánico asignado documenta diagnóstico/notas, presenta la orden para
  aprobación y marca listo desde reparación. El administrador registra
  aprobación, inicia reparación y corrige asignaciones. Cajero o administrador
  registra entrega cuando la orden está lista y cobrada.
- El administrador o mecánico asignado inicia diagnóstico desde recepción.
- Consumo de repuesto explícito durante reparación, autorizado y trazable;
  no descontar automáticamente por cambiar de estado o preparar la orden.
- Consumo repetido con la misma identidad de operación no duplica salidas.
- Cobrar una orden reutiliza ventas y enlaza consumos existentes. No vuelve a
  descontarlos. Repuestos deben tener su consumo confirmado antes de cobrar.
- Una venta activa por orden; sin cobro parcial ni entrega con saldo pendiente.
  Una venta anulada conserva su vínculo y permite una corrección posterior
  controlada; no se borra para liberar la relación.
- El cliente y vehículo quedan fijados al comenzar reparación; un cambio
  posterior de propietario no reescribe órdenes históricas.
- Cancelar una orden antes del cobro requiere motivo y administrador. Los
  consumos ya realizados no desaparecen: devolución física o compensación
  explícita según corresponda. Este estado excepcional se audita.

## 7. Ventas, pagos y caja

- Venta directa y venta de orden usan el mismo caso de uso.
- Cliente envía referencias, cantidades y datos del pago; backend obtiene
  descripción, precio vigente autorizado y costo, y calcula totales.
- Sin modificación libre del precio ni descuentos en el MVP. Una futura
  política de descuentos requerirá autorización, trazabilidad y pruebas.
- Venta directa toma precio vigente al confirmar. No hay promesa de precio
  previa al cobro ni cotizaciones en el MVP. Cambios de importe requieren
  nueva revisión del usuario antes de confirmar; nunca cobrar más en silencio.
- Efectivo, tarjeta y transferencia; se permiten pagos combinados que cubran
  exactamente el total. Sin crédito ni pagos parciales pendientes.
- El importe del pago en efectivo es el aplicado a la venta; efectivo recibido
  y cambio pueden mostrarse como cálculo de interfaz, sin inflar lo ingresado.
- Tarjeta/transferencia son registro de una operación externa confirmada por
  personal autorizado; no se simula una pasarela ni una autorización bancaria.
- Una caja abierta por empresa inicialmente. Administrador abre/cierra;
  cajero cobra y consulta la caja abierta. Caja abierta obligatoria al cobrar,
  incluso si el pago no es efectivo, para mantener el contexto operativo.
- Solo efectivo modifica efectivo esperado. Pagos no efectivos se concilian
  por separado. Cierre incluye contado, esperado y diferencia; exige motivo
  cuando existe diferencia. Caja cerrada no se reescribe.
- Administrador registra gastos, pagos de compra y movimientos manuales con
  motivo/origen. Un gasto pagado en efectivo crea un único movimiento de caja
  enlazado; no se suman ambos registros como si fueran dos salidas.
- Venta, líneas, pagos, movimientos y resultado de idempotencia se confirman
  juntos. Si falla una parte, no queda ningún efecto parcial.
- Idempotencia por empresa y operación con huella canónica. Misma clave y
  contenido devuelven la operación existente; contenido diferente se rechaza.
  Reintentos simultáneos tampoco duplican efectos. No guardar tokens ni
  datos bancarios sensibles en el registro de idempotencia.

## 8. Anulaciones y alcance

- Resolver la discrepancia del PDF incluyendo anulación total en el hito 5,
  antes de uso real. Devoluciones parciales quedan para segunda etapa.
- Solo administrador, con motivo, referencias al original e idempotencia.
  La anulación conserva el documento y crea compensaciones; no borra datos.
- Revertir pagos por el método original. Operaciones externas requieren
  confirmación y referencia del reembolso real; la aplicación no transfiere
  dinero. Efectivo requiere caja abierta actual. Una caja anterior cerrada
  conserva su historia; el nuevo egreso pertenece a la sesión actual.
- Reembolso pendiente de ejecución no se presenta como completado.
  El modelo detallará ese estado antes de implementar anulación.
- No devolver stock automáticamente al anular: cada reposición requiere
  devolución física confirmada y se vincula a la salida original, con su
  valoración original. Repuestos consumidos y servicios no generan una
  entrada ficticia. Impedir devoluciones superiores a la salida original.
- Reportes exportables, cotizaciones, comisiones, importación CSV, crédito,
  devoluciones parciales e integraciones permanecen en segunda etapa.
- No agregar módulo de empleados independiente: técnico es una membresía
  autorizada. Sin nómina ni recursos humanos.

## 9. Política fiscal y operación real

- Facturación electrónica y contabilidad legal quedan fuera del MVP.
- No interpretar esa exclusión como exención tributaria. No fijar tasas,
  exenciones, numeración fiscal ni impuestos cero sin validación.
- Antes de implementar cálculos fiscales se necesita una política documentada
  y validada para el negocio. Su ausencia no bloquea fundación, acceso o
  inventario; bloquea cobro real y salida a producción con ventas.
- El comprobante operativo solo refleja datos almacenados y no se presenta
  como documento fiscal sin la validación correspondiente.

## 10. Archivos y operaciones

- Adjuntos fuera de PostgreSQL. Inicialmente volumen privado del backend,
  interfaz sustituible por almacenamiento de objetos cuando se necesite.
- Descarga autenticada y autorizada por tenant; sin directorio público de cargas.
- En primera versión de adjuntos: JPEG/PNG, máximo 5 MiB, comprobar firma,
  decodificar y re-encodear, limitar dimensiones a 20 megapíxeles, eliminar
  metadatos y generar nombre interno. Sin SVG, HTML ni PDF subido inicialmente.
- Metadata de adjuntos conserva propietario y referencia; la limpieza de archivos
  huérfanos y los fallos entre filesystem y DB tienen tratamiento explícito.
- Backups diarios cifrados de DB y adjuntos, copia fuera de VPS, retención
  inicial de 30 días y ensayo mensual de restauración. Objetivos iniciales:
  RPO 24 horas y RTO 8 horas, pendientes de medición antes de prometerlos.
- Migraciones controladas, backup previo y recuperación documentada; no ejecutar
  migraciones automáticamente en cada proceso de la API.
- Desarrollo, pruebas y producción separados; nunca tests destructivos sobre
  la base operativa. No publicar PostgreSQL ni guardar secretos en Git/imágenes.

## Secuencia de fundación

Fundación 1.1: dependencias backend, configuración validada, app mínima sin
módulos comerciales y pruebas de configuración/arranque. Su ejecución consta
en `deliveries/foundation-1.1.md`.
Fundación 1.2 incorpora PostgreSQL/Alembic/Compose; fundación 1.3 añade
Next.js/TypeScript y proxy del mismo origen. Los resultados constan en
`deliveries/foundation-1.2.md` y `deliveries/foundation-1.3.md`.
Fundación 1.4 automatiza y valida localmente el conjunto mediante `scripts.ci`.
El workflow pasó actionlint; ejecución remota pendiente hasta conectar GitHub.
La evidencia y ese límite constan en `deliveries/foundation-1.4.md`.

## Acceso 2.2: bootstrap y contraseñas

- Bootstrap exclusivamente CLI interactivo, sin argumentos de credenciales,
  entrada por tubería ni fallback de contraseña visible. Sin endpoint público.
- Exige esquema en head y tablas de acceso vacías, incluso registros inactivos.
  Un advisory lock transaccional `(1128354386, 1)` serializa este comando bajo
  READ COMMITTED. Espera máxima 5s; commit único después de los tres inserts.
  La repetición no actualiza ni recupera usuarios. No coordina SQL arbitrario.
- Contraseña: mínimo 15 y máximo 128 caracteres Unicode, sin normalizar/recortar
  ni imponer composición. Se valida UTF-8. Política técnica previa al login;
  listas de contraseñas comprometidas y límites de intentos siguen pendientes.
- Argon2id mediante argon2-cffi, perfil explícito RFC_9106_LOW_MEMORY: m=65536 KiB,
  t=3, p=4, sal=16 bytes, hash=32 bytes. Cada hash genera una sal aleatoria.
  La utilidad de rehash se aplicará tras autenticar al implementar login.
  Habrá que medir coste y limitar concurrencia de hashing antes de exposición.
- EmailStr/email-validator comprueba sintaxis sin consultar entregabilidad DNS.
  El correo se almacena normalizado a minúsculas conforme a la decisión existente.
- El CLI muestra errores saneados sin tracebacks ni identidades. El operador debe
  controlar la terminal y el entorno; no se promete borrado seguro de strings de
  memoria Python. No crear credenciales reales automáticamente durante validación.

Referencias: [argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/api.html),
[RFC 9106](https://www.rfc-editor.org/rfc/rfc9106.html),
[OWASP autenticación](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[bloqueos PostgreSQL](https://www.postgresql.org/docs/current/functions-admin.html).

## Acceso 2.3: persistencia y servicio interno de sesiones

- Token opaco generado con secrets.token_urlsafe(32), 256 bits, sin IDs ni roles.
  Únicamente digest SHA-256 único en DB; el token sale como SecretStr al llamador.
  No se aceptan tokens elegidos al emitir ni tenant/rol al resolver.
- Sesión asociada a tenant y membresía mediante FK compuesta restrictiva. Usuario
  y rol se derivan de membresía actual, sin snapshots de permisos en el token.
- Rechazo al alcanzar exactamente 30 minutos de inactividad u ocho horas de vida.
  Actividad válida renueva solo last_seen_at. PostgreSQL clock_timestamp() leído
  tras bloqueos evita usar el inicio antiguo de una transacción que estuvo esperando.
- Orden de bloqueo: usuario FOR UPDATE y sesiones por UUID FOR UPDATE. Creación,
  resolución y revocación usan el mismo orden, con relectura tras esperar. No
  guardar contextos entre peticiones. No esperar servicios externos con estos locks.
- El llamador posee commit/rollback, también para renovar actividad. No entregar
  cookies/tokens hasta commit. El login futuro verifica credenciales bajo el mismo
  bloqueo del usuario para coordinarse con cambios concurrentes de contraseña.
- Revocación individual idempotente, por membresía para privilegios y por usuario
  para contraseña/desactivación. Los futuros servicios de administración deben
  invocarlas dentro de la transacción del cambio. No son triggers sobre SQL manual.
- Estos bloqueos serializan sesiones del mismo usuario y protegen sus operaciones
  coordinadas; la autorización y bloqueos de negocio tendrán su propio protocolo.
  Aún no hay login/logout HTTP, cookies, CSRF, límites de intentos ni auditoría.

Referencias: [gestión de sesiones OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[reloj PostgreSQL](https://www.postgresql.org/docs/current/functions-datetime.html).
