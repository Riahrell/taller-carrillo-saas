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

## Acceso 2.4: autenticación y límites compartidos

- Se mantienen los límites ya adoptados: cinco fallos por cuenta y treinta por IP
  en 15 minutos. Ventana fija iniciada por el primer fallo; vencimiento inclusivo.
  Bloquear intentos posteriores al límite, sin extender ventanas por rechazos.
  Un éxito reinicia cuenta; nunca borra el historial de fallos de una IP compartida.
- Contadores globales por SHA-256 del correo canónico o IP normalizada, con estado
  de ventana en PostgreSQL. No llevan tenant para impedir evasión al cambiarlo.
  Hash determinista no equivale a anonimización. Índice temporal para futura limpieza.
- Bloqueos en orden IP -> cuenta -> usuario -> sesiones. Verificar IP antes de crear
  un bucket de cuenta reduce acumulación de correos cuando el origen ya está limitado.
  INSERT ON CONFLICT + SELECT FOR UPDATE coordinan instancias independientes.
- El servicio Authenticator posee la transacción y devuelve None tras confirmar
  el rechazo. Credenciales correctas, rehash si procede y nueva sesión confirman
  juntos; no emitir tokens al fallar el commit. DB/hashing fallidos son un error
  de infraestructura saneado, distinto del rechazo ordinario.
- Verificación Argon2id de descarte para cuenta desconocida/hash inválido. Hash
  aleatorio por instancia de worker inicializada antes de admitir peticiones.
  No garantiza tiempo constante, y el coste se sigue limitando antes de verificar.
- El contrato de credenciales solo email/password, estricto y sin autoridad del
  cliente. Password login permite 1–128 caracteres para verificar sin reinterpretar
  políticas de alta; alta/bootstrap conserva mínimo 15. No recortar la contraseña.
- selected_tenant_id es un candidato interno con comprobación de membresía después
  de verificar contraseña. La IP validada procede de red confiable. No aceptar
  X-Forwarded-For sin verificar ni IP del body. No hay rutas de acceso en esta fase.
- Contrato HTTP/cookies/CSRF previo a implementación en auth-http-contract.md;
  double-submit firmado ligado a sesión/preautenticación, con clave externa y
  validación de origen. Su implementación y pruebas siguen pendientes.

Referencias: [autenticación OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[INSERT PostgreSQL 18](https://www.postgresql.org/docs/18/sql-insert.html).

## Acceso 2.5: cookies, origen y CSRF

- Middleware ASGI global: métodos con efectos exigen Origin exacto, prueba CSRF
  firmada igual al cookie y binding de sesión/preauth. Sec-Fetch-Site, si presente,
  debe ser same-origin. GET/HEAD/OPTIONS no alteran negocio ni emiten cookies solos.
- HMAC-SHA-256 sobre estructura con propósito, binding, caducidad y nonce aleatorio.
  Cookie preauth también firmado con propósito distinto; no representa identidad.
  Prueba/nonce: vida máxima 15 minutos. No incluir el token de sesión en la prueba.
- Cookie de sesión/preauth HttpOnly; prueba CSRF legible por JS. Todos host-only,
  Path=/ y SameSite=Lax. HTTPS usa Secure y prefijo __Host-. HTTP solo loopback
  development/test, sin prefijo ni Secure. Producción siempre HTTPS.
- PUBLIC_ORIGIN canónico y clave externa hex de 32 bytes obligatorios al arrancar
  API; sin reserva. CLI/migraciones no necesitan clave HTTP. Montaje solo API/tests.
  Generación/upgrade local no reescribe credenciales existentes. Clave entre workers
  compartida; rotación coordinada. El formato no demuestra entropía: usar generador.
- JSON UTF-8 hasta 8 KiB para escrituras actuales, tanto proxy como API, incluso
  fragmentos ASGI. Adjuntos futuros requieren política explícita de contenido/tamaño.
- Validación de Pydantic saneada sin inputs; rechazos de guard 403, contenido 415,
  tamaño 413 y JSON inválido 400, sin reflejar datos. Escrituras/rechazos no-store.
- No hay rutas nuevas de acceso: helpers y rutas de prueba no autentican usuarios.
  Un CSRF válido no sustituye sesión viva/autorización DB ni publica tokens antes
  del commit. IP confiable, selección multiempresa y handlers siguen pendientes.
- CI configura el origen al puerto efímero real, recrea backend y recarga Nginx
  conservando el contenedor/puerto. Se detecta si el puerto cambia antes del smoke.

Referencia: [CSRF OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
[middleware Starlette](https://www.starlette.io/middleware/).

## Acceso 2.6: identidad verificada y selección de empresa

- Una membresía activa se elige en servidor; varias requieren confirmación
  explícita desde identidad ya verificada. Cero membresías rechaza genéricamente.
  No devolver nombres de empresas por correo antes de verificar contraseña.
- Servicio interno begin_login: credenciales estrictas, IP del adaptador confiable
  y binding de cookie HttpOnly del servidor. El adaptador futuro debe verificar
  origen, CSRF y cookie; el formato por sí solo no prueba su procedencia.
- Estado provisional independiente de sesión y nonce CSRF: token aleatorio de
  256 bits; únicamente SHA-256 de token y binding en DB. Vigencia técnica inicial
  de cinco minutos desde reloj DB. No conceder permisos con ese estado.
- Selección de una membresía del mismo usuario, con los estados activos releídos.
  Usuario y rol no se reciben del cliente. Uso único ligado al navegador: un
  intento con binding correcto consume el pendiente incluso si la opción es
  inválida. Con otro binding no se puede utilizar ni consumir el pendiente.
- Una nueva autenticación correcta cierra pendientes anteriores del usuario;
  contraseñas rechazadas no lo hacen. La invalidación de pendientes no cierra
  sesiones ya emitidas ni cambia su tenant. El cambio desde interfaz queda pendiente.
- Huella SHA-256 del hash Argon2 invalida pendientes cuando cambia contraseña/hash,
  incluido un rehash externo de la misma contraseña. El rehash de begin_login
  sucede antes de guardar esa huella. No persistir contraseña en el pendiente.
- Lock de usuario antes de pendientes/sesiones; releer después de esperar y usar
  reloj DB posterior al lock. Consumir e insertar sesión en la misma transacción;
  solo devolver token tras commit. Los rechazos de selección también confirman.
- Revocación de pendientes por usuario disponible dentro de la transacción del
  llamador. Futuros cambios de cuenta/contraseña deberán usarla junto a revocación
  de sesiones. Sin triggers ni garantía frente a SQL manual arbitrario.
- No se agregan rutas, campos comerciales ni dependencias en esta entrega.
  HTTP, IP confiable por proxy real, límites de volumen/espera, mantenimiento,
  permisos, auditoría y formulario siguen pendientes antes de habilitar acceso.

La vigencia y el intento único son decisiones técnicas de este proyecto; no se
presentan como valores impuestos por las referencias.
Referencias: [autenticación OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[bloqueos PostgreSQL 18](https://www.postgresql.org/docs/18/explicit-locking.html).

## Acceso 2.7: transporte HTTP y proxy confiable

- Rutas de acceso documentadas en auth-http-contract.md: emisor/renovador CSRF,
  login, selector, consulta de sesión y logout. Los cuerpos solo contienen los
  campos técnicos del caso de uso; identidad/tenant/rol se resuelven en servidor.
  Todas las respuestas de acceso son no-store; tokens solo en cookies HttpOnly.
- Lecturas de sesión/CSRF exigen X-Requested-With: XMLHttpRequest, CORS cerrado,
  Origin exacto cuando existe y Sec-Fetch-Site same-origin cuando existe.
  El GET mismo origen puede no incluir Origin. Una página de otro origen no
  puede enviar esa cabecera sin preflight, que no recibe permiso CORS.
- Escrituras mantienen Origin obligatorio, JSON acotado y prueba CSRF firmada.
  No hay excepciones del guard para login/logout. Cookie preauth firmada verificada
  como binding; cookie de identidad pendiente independiente y privada, cinco minutos.
  Renovar CSRF conserva un nonce vivo durante selección para mantener su binding.
- Login/selección requieren ausencia de cookie de sesión. Otro acceso exige
  logout, sin cambiar el tenant de una sesión existente. El emisor CSRF comprueba
  una sesión presente y recupera cookies caducadas/revocadas con preauth nueva.
- Consulta de sesión confirma actividad antes de responder contexto actual.
  Logout revoca sesión y pendiente con nonce válido juntos, también con cookies
  mixtas; ordena locks de usuarios por UUID antes de pendientes/sesiones. Un fallo
  de commit conserva las cookies y revierte las mutaciones; no autentica a medias.
- Redes Compose: edge para frontend/proxy; api interna para proxy/backend;
  private interna para backend/DB/herramientas. Solo proxy publica loopback.
  Los tests CI se conectan también a api, únicamente en el override aislado.
- Nginx establece X-Real-IP desde su socket observado y elimina Forwarded,
  X-Forwarded-For y X-Forwarded-Host. Uvicorn usa --no-proxy-headers. La API acepta
  X-Real-IP solo si su socket peer coincide con TRUSTED_PROXY_HOST resuelto en
  DNS Docker, sin caché de IP fija ni reserva si falla DNS. El daemon/red/DNS son
  autoridad del operador. No extender confianza a todas las IP privadas o a
  proxies añadidos sin rediseñar y probar la cadena de confianza.
- Dos verificaciones de contraseña simultáneas por worker; exceso 503 sin cola.
  Uvicorn limita 64 conexiones/tareas. Nginx aplica 10 peticiones/s por IP a acceso,
  burst 20 sin espera y diez conexiones activas por IP; exceso 429 genérico.
  Cuerpo máximo 8 KiB, espera de cuerpo 10s y buffering antes del backend.
  Nginx normaliza fallos upstream 502/503/504 de acceso a 503 JSON/no-store.
  Son parámetros iniciales técnicos; falta medir capacidad para producción.
- Transacciones de acceso fijan localmente lock_timeout 3s y statement_timeout
  10s; no cambian el resto de conexiones ni prometen un timeout total HTTP/DNS.
  Los límites de fallos por cuenta/IP conservan los valores y el 401 genérico.
- Uvicorn sin access log; Nginx registra método/URI sin query/status e IP, sin
  cuerpos ni cookies. Se descartan errores de request de /api/auth/ que pueden
  incluir query; se conserva el status en access log. CI examina un canario
  sintético de cuerpo/query/cabeceras, incluidos rechazos 429,
  en logs capturados sin imprimirlos. Secretos en trazas/payloads siguen prohibidos.
- No hay cambios de esquema, dependencias ni frontend. La revisión sigue
  0005_login_challenges. Formulario, permisos, administración, mantenimiento,
  HTTPS real y hardening de producción siguen pendientes en sus hitos.

Referencias: [CSRF OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
[redes Docker Compose](https://docs.docker.com/compose/how-tos/networking/).
Flags Uvicorn contrastados con el CLI instalado de la dependencia fijada.

## Acceso 2.8: formulario y estado de navegador

- Componente cliente acotado a acceso: comprobar sesión, formulario, selector,
  contexto confirmado y estado incierto. No simular módulos comerciales ni saldos.
- Contraseña no controlada por React y borrada al submit; preservar espacios y
  contar Unicode como servidor. Sin credenciales/contexto en Storage, cookies
  legibles de identidad, URLs ni mensajes entre pestañas. HTML method=post.
- Antes de cada POST, renovar CSRF y leer solo la cookie de prueba del protocolo
  actual. Sin Origin/Cookie manuales, rutas externas ni redirects. Decodificar
  respuestas antes de usarlas y mostrar únicamente errores seguros.
- Sin opción de empresa preseleccionada; confirmar sesión con lectura después
  de autenticar/seleccionar. Logout/cancelación usan revocación HTTP y descartan
  contexto anterior. Fallo de cierre no se anuncia como sesión cerrada.
- Un envío simultáneo por componente; sin retry automático de escritura. Timeout
  cliente de 15s por request, con resultado potencialmente incierto: abortar no
  revierte servidor. Recuperar mediante lectura, o reintento explícito de logout.
- Relectura al volver a foco/visibilidad o recibir changed por BroadcastChannel,
  sin identidad en el mensaje. Descartar lecturas abortadas/tardías. No polling.
  Los timers de 5/30 min solo ocultan selector/contexto: no renuevan ni revocan DB.
- Una recarga/foco que reinicia selector pierde opciones efímeras y requiere
  credenciales de nuevo. No guardar el pendiente en JS ni añadir un endpoint de
  recuperación de opciones en esta tarea. La cookie provisional sigue siendo HttpOnly.
- Prueba de navegador con cuenta/empresas sintéticas en base efímera protegida por
  los guards de test y variable explícita. Fixture fuera de colección automática
  y de imagen runtime, credenciales en archivo privado tmpfs y limpieza al salir.
  No crear una cuenta real ni revelar contraseñas de operador para validar UI.
- Sin cambios de endpoints, esquema, dependencias ni reglas comerciales. Hito 2
  continúa con autorización y administración; producción sigue pendiente.

Referencias: [efectos React](https://react.dev/reference/react/useEffect),
[componentes cliente Next.js](https://nextjs.org/docs/app/api-reference/directives/use-client).

## Acceso 2.9: administración interna y auditoría de membresías

- Una operación interna acotada: actualizar rol/estado de una membresía existente.
  Transacción propia READ COMMITTED; sin router, CLI operativo ni UI todavía.
  La identidad y empresa se obtienen del token de sesión; no admite actor, tenant
  ni un MembershipContext externo. Solo admin actual, releído tras los locks.
  role/is_active son estado deseado explícito, con enum y booleano estricto;
  Pydantic rechaza campos extra. No son credenciales ni autoridad.
- Bloqueo de la empresa de la sesión antes de todos los usuarios implicados en
  orden UUID. Después se verifica la sesión actual y se bloquea/relee la membresía
  objetivo de esa misma empresa. Los hints iniciales no autorizan. Se mantienen
  los locks hasta commit/rollback. lock_timeout 3s y statement_timeout 10s locales.
- El lock de empresa serializa cambios de membresías y el conteo de administradores.
  Las futuras altas, cambios globales de usuario o modificaciones de empresas
  deberán prebloquear todas las empresas afectadas por UUID antes de usuarios.
  Login/sesión/logout no adquieren posteriormente locks de empresa y mantienen
  sus protocolos existentes. Ninguna operación nueva debe invertir esa jerarquía.
- Todos los usuarios se bloquean antes de cualquier hijo, también con actor y
  objetivo cruzados en dos empresas. Tras resolver la sesión del actor se pueden
  cancelar pendientes del objetivo: ambos padres ya están bloqueados, por lo que
  otro flujo no puede conservar hijos mientras espera esos usuarios. Dentro de la
  revocación del objetivo, pendientes preceden a sus sesiones. No trasladar esta
  excepción a operaciones que todavía no bloquearon todos los padres.
- Un admin utilizable tiene membresía y usuario activos, en la empresa activa.
  No desactivar ni degradar al último. Otros admins con cuenta o membresía
  inactivas no cuentan. Auto-desactivación/degradación permitida si queda otro,
  revocando también la sesión que solicita el cambio. No hay recuperación implícita
  ni promesa de proteger SQL manual o cambios globales aún no implementados.
- Cambio real: revocar todas las sesiones de la membresía objetivo, cancelar todos
  los pendientes globales del usuario y registrar antes/después/autor en el mismo
  commit. Otras sesiones de ese usuario en otras empresas continúan vigentes.
  Reactivar exige nuevo login; no revive tokens. Un no-op no revoca ni audita,
  aunque renueva actividad del actor. No es idempotencia general: si el actor se
  quitó acceso, un reintento con su token revocado se rechaza.
- `audit_events` comienza tipada solo para membership_changed, con roles/estados
  antes/después, empresa, actor/objetivo mediante FK compuestas y fecha de DB.
  Sin JSON libre, correo, token, IP, contraseña ni campos comerciales inventados.
  CHECK de roles/acción/cambio efectivo y FK RESTRICT sin cascadas. El runtime
  conserva SELECT/INSERT; se retiran UPDATE/DELETE en la migración. El owner sigue
  pudiendo mantener la DB: no es un registro a prueba de administradores de DB.
- Ningún resultado sale antes de commit; SQL/lock/commit fallidos se traducen a
  error saneado y rollback. No retry automático. La API futura deberá aplicar
  CSRF, política de cookies, traducción HTTP segura y denegación por defecto.

Referencias: [bloqueos PostgreSQL](https://www.postgresql.org/docs/current/explicit-locking.html),
[transacciones SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block),
[privilegios PostgreSQL](https://www.postgresql.org/docs/current/sql-revoke.html).

## Acceso 2.10: consulta y cambio HTTP de membresías

- Dos rutas acotadas bajo /api/auth/memberships/{membership_id}: GET del estado
  y PATCH del estado deseado explícito. Sin listado, altas ni operaciones globales
  de usuario. El prefijo reutiliza las protecciones/límites/logs del proxy de acceso;
  no se añaden reglas de Nginx ni puertos. Contrato en membership-http-contract.md.
- Router delgado y servicio transaccional compartido para lectura/escritura.
  Extraer la autorización de 2.9 conserva empresa -> usuarios por UUID -> hijos.
  Identidad inválida: 401; falta de permiso/objetivo desconocido o ajeno: 403 igual.
  El permiso se relee incluso después de esperar. No aceptar contextos externos.
- Vista mínima membership_id/role/is_active. Sin usuarios globales, correo,
  tenant elegido, hash ni token. GET requiere intención de navegador del mismo
  origen y PATCH exige origen/CSRF bajo el middleware global existente. Query
  params prohibidos; cuerpo Pydantic explícito y estricto, sin campos extra.
- GET/PATCH devuelven ETag entre comillas de SHA-256 de representación estable.
  PATCH requiere If-Match de un solo tag fuerte en este formato; 428 si falta,
  400 si es inválido, débil, lista, duplicado o wildcard. No es un secreto ni prueba
  de lectura previa. Comparar después de autorización y bajo lock, antes de cambiar:
  412 si el estado difiere, sin revocaciones/eventos. Sin retry automático.
- No agregar columna de versión ni migración por ahora. El tag valida estado
  actual, no historia monotónica: si vuelve el mismo estado, vuelve el tag. No
  usarlo como idempotencia comercial ni como garantía contra cambios intermedios
  restituidos. Cambiar eso requerirá un contrato posterior explícito.
- Errores de dominio se traducen centralmente en la factory: 409 último admin,
  412 estado cambiado, 401/403 acceso y 503 saneado. La indisponibilidad comparte
  Authentication unavailable con Nginx /api/auth/. Validaciones no devuelven inputs.
- Un resultado interno indica si el cambio revocó al actor. Solo tras commit
  se eliminan sus cuatro cookies con el scope existente; ese flag no sale en JSON.
  No-op propio conserva cookies. Fallo de commit preserva cookies y devuelve 503.
  Si se pierde respuesta, comprobar sesión/estado o recurrir a otro administrador.
- Sin cambios en esquema/dependencias/frontend. Cierre probado por peticiones
  directas y proxy real con identidades efímeras, no con cuentas de desarrollo.
  Todavía sin pantalla o prueba visual administrativa ni TLS real.

Referencias: [If-Match RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-match),
[428 RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html#section-3),
[manejo de errores FastAPI](https://fastapi.tiangolo.com/tutorial/handling-errors/).

## Acceso 2.11: listado paginado de membresías

- GET /api/auth/memberships, ruta canónica sin barra final. Query model Pydantic
  con extra forbid, limit por defecto 25 y máximo 100, after UUID opcional.
  Rechazar duplicados y valores no canónicos de limit. Ninguna opción de tenant,
  rol, búsqueda, offset u orden elegidos por el cliente.
- Estado mínimo útil para administrar: membership_id, email existente, role,
  is_active de membresía y user_is_active de cuenta. El correo identifica al
  usuario dentro de la empresa autorizada. Sin ID global, hash, token ni datos
  de otras empresas. Mostrar estados inactivos no los habilita ni permite cambiar
  cuentas globales. Sin nuevo campo persistido o regla comercial.
- Autorización común para detalle/cambio/listado. Prebloquear empresa del token,
  luego actor, verificar sesión y admin actual. La consulta de página es un SELECT
  acotado con join de usuarios; no bloquea sus filas después de la sesión del
  actor ni invierte el orden de padres. Mantener locks de autoridad hasta commit.
- Cursor directo por UUID, frontera exclusiva id > after y ORDER BY id ascendente.
  Aprovechar índice único (tenant_id, id) existente, LIMIT limit+1 para saber si
  queda otra página; sin COUNT global ni OFFSET. No buscar existencia del cursor.
  Page contiene items y next_after, null si no se observó registro adicional.
- Listado vivo, sin snapshot entre requests: inserciones anteriores al cursor
  requieren reiniciar para observarse; cambios posteriores se leen al consultar.
  No se promete exportación estable ni total. Una nueva consulta del detalle
  proporciona el estado a revisar y su ETag antes de escribir.
- Respuesta solo tras commit y sin cookies/ETag de colección, no-store y CORS
  cerrado. GET exige intención de navegador según política existente; cada página
  vuelve a autenticar/autorizar y toca actividad. No auditoría/revocación por listar.
  El lock de empresa serializa lecturas administrativas; medir capacidad antes de
  ampliar concurrencia. Sin migraciones, dependencias o UI nuevas.

Referencias: [query models FastAPI](https://fastapi.tiangolo.com/tutorial/query-param-models/),
[orden y LIMIT PostgreSQL](https://www.postgresql.org/docs/current/queries-limit.html).

## Acceso 2.12: pantalla administrativa de membresías

- Ruta `/membresias` con shell sin datos personales en HTML inicial y enlace
  desde sesión admin. Verificar sesión antes de montar lista; nunca derivar
  autoridad de URL, Storage, canal entre pestañas o perfil elegido en un formulario.
- Una página de 25 accesos y un borrador en memoria. Revisar detalle actual y
  ETag antes de editar; resumen y confirmación separados. Impedir doble envío
  mientras hay escritura. Cancelar o volver a editar no cambia datos.
- Añadir X-Access-Membership en éxitos de administración para vincular respuesta
  a la sesión verificada. Capturar actor dentro de la transacción, excluir del
  JSON/ETag. PATCH admite X-Expected-Membership opcional para compatibilidad;
  la UI siempre lo envía. Compararlo contra el actor autorizado antes de escribir,
  sin permitir elegir identidad/empresa. Sin migraciones ni nuevos permisos.
- Reutilizar origen/CSRF, If-Match, locks, último admin, auditoría y revocación.
  409/412 requieren otra revisión explícita. Fallos inciertos retiran contexto;
  no retries automáticos de PATCH. Tras éxito ajeno releer sesión y primera página;
  tras éxito propio comprobar acceso. Abort cliente no revierte servidor.
- Focus/pageshow/visibilidad y `changed` sin payload personal retiran/recomprueban
  contexto; timer visual de 30 minutos sin polling. Sin promesa de actualización
  instantánea de una pestaña inactiva ante cambios remotos. Backend autoriza cada
  petición. Cuenta global inactiva se informa; no se activa desde esta pantalla.
- QA manual con proyecto aleatorio, DB tmpfs e identidades desechables; harness
  `scripts.browser_qa` reutiliza guards/limpieza del CI. Credenciales solo en un
  archivo privado dentro del fixture. Nunca crear usuarios reales para pruebas.

## Acceso 2.13: alta interna de cuenta nueva y membresía

- Implementar primero el contrato/servicio y sus pruebas, sin API/UI ni cuentas
  reales. Reutilizar la autoridad transaccional del administrador y el lock de
  empresa; expected_actor obligatorio solo comprueba contexto previamente leído.
- Identidad global: correo existente produce conflicto genérico, sin adjuntar,
  reactivar o reemplazar contraseña. Un enlace a otra empresa requiere un flujo
  posterior con control de identidad; no inferir consentimiento por conocer correo.
- Cuenta y membresía activas creadas por servidor, rol objetivo explícito entre
  los cuatro ya aprobados. SecretStr de 15–128 caracteres y hash existente;
  sin generación/entrega de contraseñas, sesión automática o contraseña de reserva.
- Registrar `membership_created` en audit_events, con estado previo null y estado
  posterior real, en el mismo commit. Ampliar CHECKs mediante migración 0007,
  conservar privilegios y rechazar downgrade si existe evidencia no representable
  en 0006. No borrar historial para permitir una reversión.
- Detalles y límites: `user-provisioning-contract.md`. El adaptador posterior debe
  concretar CSRF, límites y elección/entrega segura de contraseña antes de exponer
  altas a usuarios. No declarar el hito de acceso completo con este servicio.

## Acceso 2.14: adaptador HTTP de alta administrativa

- POST de la colección `/api/auth/memberships`, sin nueva API global de usuarios.
  Usar UserCreation del servicio, sin query ni campos de autoridad extra. Sesión
  admin vigente y CSRF/origen obligatorios; X-Expected-Membership requerido como
  precondición de contexto. GET/PATCH mantienen su contrato anterior.
- 201 tras commit con representación mínima de membresía, ETag y actor. Location
  relativo `./memberships/{id}` resuelve tanto con `/api` como en acceso directo,
  sin tomar origen de Host. Sin emitir cookies, sesión o credenciales del usuario.
- 409 User creation unavailable para correo global existente, sin información de
  otras empresas. Validación/errores sin eco ni metadatos de éxito, siempre
  no-store. POST repetido no reasigna ni resetea; resultado perdido exige revisar.
- Compartir los dos slots Argon2 por proceso con login, sin cola al saturar (503).
  Reutilizar límites de cuerpo, conexiones y volumen del proxy de acceso y logs
  sin query/cookies/cuerpo. Sin nueva dependencia, migración o configuración.
- Canal definido para el formulario posterior: alta presencial/asistida en equipo
  controlado, donde la persona nueva elige/escribe su clave en un campo oculto.
  Requiere confianza en el administrador y equipo. Sin enviar claves por chat,
  correo o URL. HTTPS fuera de loopback. No afirmar invitación remota, verificación
  de correo, cambio inicial forzoso o recuperación; esos mecanismos siguen pendientes.

## Acceso 2.15 y prioridad de seguridad

- Formulario de alta asistida en `/membresias/nueva`, bajo el workspace de sesión
  existente. Elegir correo/perfil, revisar y luego introducir contraseña/confirmación
  en campos ocultos no controlados. Sin perfil predeterminado ni clave en resumen.
- Reutilizar transporte mismo origen y contexto obligatorio. Requerir 201 con
  actor/rol/estado/ETag/Location coincidentes; confirmar sesión y detalle antes de
  éxito. 409/422 exigen revisión; otros errores retiran contexto. Sin retries.
- Retirar claves DOM en envío, edición, cancelación y desmontaje; no Storage ni
  promesa de borrado criptográfico de strings JS. El equipo y administrador deben
  ser confiables. Continuar pruebas aisladas, nunca con cuentas reales del usuario.
- El usuario prioriza seguridad incluso demorando inventario. Revocar el paso
  inmediato a catálogo propuesto durante la conversación. Registrar amenazas,
  controles/brechas y puerta de paso en `security-review.md`, tomando OWASP ASVS
  como referencia, sin alegar cumplimiento completo o certificación.
- Siguiente tarea: reautenticación reciente de acciones administrativas, antes
  de inventario. Recuperación/MFA, auditoría, dependencias, navegador y backup
  requieren revisión/evidencia; no se completan por añadir esta documentación.
- Dominio previsto en Namecheap y VPS Contabo que ya aloja imágenes. Registrar
  staging aislado y plan DNS/HTTPS/renovación, sin conectar una DB operativa,
  cambiar el VPS o publicar esta entrega. Ver `staging-plan.md`.

## Acceso 2.16: confirmación interna y rotación

- Ventana reciente de cinco minutos, reloj de PostgreSQL y huella del hash de
  contraseña actual, por sesión/membresía. No se renueva con actividad ordinaria,
  comparte prueba entre sesiones/empresas ni extiende las ocho horas absolutas.
- Contraseña actual como único dato del comando, 1–128 puntos de código UTF-8,
  sin transformación ni cambio de política retroactivo. Admin actual y contexto
  esperado obtenidos/comparados con sesión; sin email, tenant o fecha del cliente.
- Rotar token opaco en el mismo commit que la prueba, invalidar inmediatamente
  el viejo y publicar el nuevo solo tras commit. Respuesta perdida requiere login,
  sin replay automático. Repetir contraseña no es MFA ni defensa contra phishing.
- Reutilizar límites persistentes de cuenta/IP del login. Savepoint revierte
  cambios de sesión ante rechazo mientras se confirman contadores. Mantener
  límites de locks y orden buckets -> empresa -> usuario -> sesión.
- Migración 0008: columnas opcionales con CHECK, sin defaults/backfill. Downgrade
  revoca sesiones confirmadas antes de retirar datos; no recupera confirmaciones
  al subir de nuevo. No tocar identidades, membresías o auditoría comercial.
- Entrega pequeña: contrato, servicio y guardia interna probados, sin HTTP/UI y
  sin activar la guardia desde operaciones actuales. Siguiente 2.17 define y
  conecta adaptador con CSRF/origen, slots compartidos y cookies rotadas;
  posteriormente integrar UI y exigir guardia dentro de alta/cambio.
- El VPS, imágenes y dominio permanecen fuera de esta entrega. No se necesitan
  compras; confirmar requisitos/costos concretos al preparar despliegue y MFA.
- Actualización del usuario: ya adquirió taller-carrillo.com y apuntó DNS a su
  VPS. Registrar el nombre como contexto, sin IP pública ni origen hardcodeado.
  DNS, proxy y TLS todavía sin verificar; no hubo cambios remotos desde aquí.

## Acceso 2.17: adaptador de confirmación

- Definir primero POST `/api/auth/reauthenticate` sobre el servicio de 2.16;
  contraseña único campo, sin query y con contexto esperado obligatorio. Es una
  ampliación de acceso, sin nuevas reglas comerciales o permisos de empresa.
- 204 sin cuerpo, X-Access-Membership vinculado a la comparación del servicio y
  cookies sesión/CSRF rotadas únicamente tras commit. Conservar expiración,
  flags y política de cookies existentes; sin token o huella en JSON.
- Validar IP del proxy y compartir slots/cuotas con login/alta. Rechazar cookies
  mixtas con preautenticación pendiente, origen/CSRF y headers ambiguos antes de
  verificar contraseña. Rechazo 401 genérico no borra cookies ni publica actor.
- Extraer comparación de contexto común para administración/confirmación, sin
  cambiar reglas del POST/PATCH administrativo. Guardia reciente todavía no
  activa en alta/cambio; la integración de UI y transacciones corresponde a 2.18.
- Rechazar claves JSON duplicadas en middleware compartido para evitar payloads
  ambiguos; 400 saneado sin efectos, incluidas escrituras previas.
- Nueva instrucción del usuario permite dejar limpia la VPS si es necesario.
  Sustituye preservar obligatoriamente el despliegue anterior; inventariar y
  respaldar recursos antes de retirarlos. No se ha accedido o borrado nada remoto.

## Acceso 2.18: confirmación exigida en administración

- Exigir la guardia de 2.16 dentro de alta/cambio, después de locks/autorización
  y antes de ETag/cambio, también no-op. No introducir bypass de runtime.
- Alta vuelve a comprobar vigencia después de hash/inserciones/auditoría, antes
  de commit. Caducidad revierte todo. Cambio propio conserva revocación de sesión;
  no ejecutar una guardia posterior que rechazaría esa revocación legítima.
- 428 genérico sin cookies ni metadatos de éxito; lecturas siguen disponibles
  con permiso admin sin exigir confirmación reciente en la API.
- Formulario de contraseña actual antes de montar controles administrativos,
  vaciado previo a red/desmontaje, contexto rereleído después de rotación. Cada
  navegación/focus/visibilidad requiere nueva comprobación y confirmación.
- Ventana local conservadora hasta 270s desde inicio del envío; servidor manda
  con cinco minutos y reloj DB. Sin Storage, permiso persistido o replay automático.
- Web Locks serializa peticiones entre pestañas cooperantes del mismo origen;
  incluir GET por posibles respuestas que limpien cookies. Confirmación requiere
  navegador compatible/HTTPS o localhost. AbortSignal acota cola/red; no promete
  revertir efectos remotos ni eliminar carreras con clientes antiguos/manipulados.
- Adoptar el orden de revisión v1.1: 2.19 escaneos de dependencias/secretos,
  después MFA admin y recuperación, antes de inventario. Sin servicios adquiridos,
  secretos nuevos, migraciones o trabajo en la VPS durante esta entrega.

## Recuperación por correo: decisión posterior a 2.18

- Solicitud del usuario (2026-10-17): recuperar acceso mediante correo. Incorporar
  «Olvidé mi contraseña» enviado al buzón previamente verificado del usuario global.
- Enlace aleatorio, uso único, caducidad y cambio/revocaciones atómicos, sin
  revelar existencia de cuentas ni permitir que admin de otra empresa resetee
  identidades. Restablecer contraseña conserva MFA y no reactiva accesos.
- No marcar correos existentes verificados automáticamente. Diseñar incorporación
  y recuperación de MFA junto con este flujo; no crear bypass de soporte.
- Detalle y aceptación en `email-recovery-design.md`. Solo diseño documental,
  sin rutas, migración, credenciales, cambios de cuenta o envíos reales. No altera
  la siguiente tarea 2.19 de escaneos ni requiere comprar servicios ahora.

## Acceso 2.19: escaneos de seguridad obligatorios

- Ejecutar Gitleaks de archivos/historial, pip-audit de locks runtime/dev/tools y
  npm audit de lock completo antes de construir/arrancar las pruebas de aplicación.
  Herramientas fijadas; historial completo; sin audit fix, paquetes de aplicación
  ejecutados por el auditor o acceso a credenciales operativas.
- Secretos sin red sobre snapshot/mirror privados. Herramientas con red reciben
  solo manifiestos/locks y consultan registros públicos. Fallo de escáner de
  secretos bloquea esas consultas. No montar Docker socket en contenedores.
- Canary sintético demuestra detección/redacción incluso después de borrar el
  archivo del último commit. Guardar solo informe saneado, con hashes de entradas.
  Dependencias omitidas, error/red y resultados inconsistentes bloquean; no dar
  por limpio un escaneo incompleto ni permitir omitirlo desde scripts.ci.
- Un aviso raíz sin parche de braces afecta cuatro entradas dev-only de ESLint.
  SEC-2026-001 lo acepta técnicamente hasta 2026-10-31 00:00 UTC, sin ocultarlo.
  Fijar versiones/ubicación/dev=true/advisory exactos; máximo 14 días. No cubrir
  nuevos avisos, dependencias runtime, apariciones anidadas o severidad crítica.
  No retirar reglas Next o hacer downgrade 16 → 14 para limpiar el informe.
- La excepción permite validar desarrollo; no declara preparación productiva.
  Revisar al actualizar el proveedor y antes de vencer, sin extensión automática.
  Detalles y evidencia en `security-scanning.md` y `deliveries/access-2.19.md`.
- Siguiente 2.20: contrato de MFA admin/recuperación, incluyendo buzón verificado.
  Recuperación por correo sigue como diseño. Sin cambios de VPS/DNS/TLS, compras,
  credenciales operativas, migraciones o frontend en esta entrega.

## Acceso 2.20: contrato MFA y recuperación

- Contrato en `mfa-recovery-contract.md`, sin cambios ejecutables. TOTP inicial
  para admin, factor del usuario global y obligación persistente tras conceder
  admin. No eludir por membresía menos privilegiada o emisión interna de sesión.
- Autenticación pendiente no muestra empresas ni permite operar antes del factor
  exigido. Login/selección comparten deadline; no aceptar flags de verificación
  del cliente. Emisión y resolución deben comprobar evidencia/revisión del servidor.
- Alta inicial admin exige contraseña y prueba fresca del buzón exacto; sin
  backfill de correos verificados, semilla enviada por correo o bypass de soporte.
  Semilla cifrada con AEAD/keyring externo, contador TOTP consumido globalmente.
- Cuota MFA independiente de contraseña, persistente entre propósitos/pendientes;
  acertar contraseña no restaura intentos del segundo factor.
- Códigos offline de uso único con contraseña solo abren recuperación restringida.
  Sustitución final/códigos nuevos, revisión, revocaciones y evento en un commit.
  Correo restablece contraseña, preserva factor/códigos MFA sin usar y no crea sesión.
- Un login MFA completo podrá aportar prueba reciente hasta cinco minutos desde
  el factor más antiguo, reduciendo confirmaciones repetidas sin relajar la
  guardia transaccional. Cambio futuro de UX/HTTP, no comportamiento ya existente.
- TOTP no es resistente al phishing. Passkeys, recuperación excepcional y ensayos
  de reloj/restauración son pendientes; no declarar certificación AAL2/ASVS.
- Un admin nuevo con factor pendiente no sustituirá al último admin con factor
  confirmado al endurecer la protección de último administrador. Validar ese
  caso junto con alta/promoción e incorporación concurrentes antes de integrarlo.
- Siguiente 2.21: primitivas TOTP/cifrado/códigos offline con dependencias auditadas
  y pruebas. Después persistencia/servicios y HTTP/UI/correo. Sin claves operativas,
  migración, endpoints, compras, envíos o cambios de VPS en esta entrega documental.

## Acceso 2.21: primitivas MFA sin autoridad de sesión

- PyOTP 2.10.0 implementa TOTP; cryptography 50.0.2 aporta AES-256-GCM.
  Locks con hashes conservan las otras versiones existentes. Perfil fijo SHA-1,
  seis dígitos ASCII, semilla CSPRNG de 20 bytes, pasos de 30 segundos, ±1 paso.
- Se evalúan todos los candidatos y se rechaza coincidencia ambigua antes de
  filtrar contadores consumidos. Estado aceptado exige contador y hora de servidor
  juntos; una hora regresiva se rechaza incluso dentro del mismo paso. El reloj
  de primer uso necesita supervisión operativa; esta biblioteca no la proporciona.
- AEAD v1: nonce aleatorio de 12 bytes, clave de 32 bytes externa/injectada,
  AAD con dominio/versión, UUID de usuario/credencial y key id. Rotación lee claves
  anteriores y escribe solo con la activa. Desprotección falla cerrada con mensaje
  genérico ante versión, clave, etiqueta, nonce o identidad incorrectos.
- Diez códigos independientes de 16 bytes, Base32 canónico sin padding; rechazar
  variantes en vez de normalizar Unicode/espacios/case. Hash SHA-256 separado por
  propósito, usuario y conjunto. No depende de contraseña. Comparación constante.
- Semillas/códigos usan SecretBytes/SecretStr, campos congelados y excluidos de
  repr/dumps. No equivale a borrado seguro de memoria. Nunca registrar excepciones
  Pydantic con inputs (`errors()`/`json()` predeterminados) o acceder al secreto
  salvo operación criptográfica/entrega restringida futura. No hay serializador
  para entregar códigos en HTTP todavía.
- No env vars nuevas, claves operativas, rutas, tablas, QR o cambios de login.
  Los resultados son candidatos; no prueban posesión para emitir sesión ni consumen
  nada. 2.22 añadirá persistencia y consumo atómico antes de integrar autenticación.

## Acceso 2.22: consumo MFA transaccional interno

- 0009 crea solo credenciales globales cifradas, conjuntos de recuperación y hashes
  de códigos. Un factor no revocado por usuario y un conjunto vigente por factor,
  con índices únicos parciales. FK compuesta impide conjuntos con propietario ajeno.
- La credencial representa confirmación ya completada; el alta pendiente futura
  necesita otra persistencia cifrada. No backfill, cuentas reales con factor nuevo
  ni cambios de emisión/resolución de sesión en esta entrega.
- Orden usuario -> credencial -> conjunto -> código, refresh ORM y hora de DB tras
  locks. Consumo global del contador TOTP, independiente del propósito del llamador;
  códigos offline con marca individual. La transacción pertenece al caso de uso,
  sin commit o sesión desde la biblioteca, y todo se revierte si falla trabajo posterior.
- No confundir consumo con prueba autorizada de sesión/recuperación: faltan
  contraseña/pendientes, cuotas, auditoría global, revisión y revocación integradas.
  El hash offline conserva el vínculo a usuario/conjunto y no a la contraseña.
- Sin DELETE para la app en tablas MFA; ciclo de vida por revocación/invalidez.
  Downgrade bloqueado ante cualquier factor, incluso revocado, para no olvidar
  evidencia. No eliminar filas para forzarlo. Roles propietarios/SQL arbitrario
  siguen fuera de esta garantía de aplicación.
- Contrato físico/límites en `mfa-persistence-contract.md`. Siguiente 2.23:
  auditoría global append-only para consumo MFA en el mismo commit; después
  cuotas compartidas e integración de los restantes controles antes de activar MFA.

## Acceso 2.23: evidencia global atómica de consumo MFA

- 0010 añade `security_events` con sujeto global, factor, acción enumerada y hora
  de servidor. Solo dos acciones de consumo; no confundir con login autorizado.
  FK compuesta comprueba propietario. No tenant/actor comercial falso, códigos,
  semillas, hashes de recuperación o payload libre. Sin backfill de éxitos previos.
- Cada consumo y evento usan la misma transacción/flush, sin commit interno;
  fallar la escritura de auditoría revierte el consumo. Replay o rechazo no
  fabrican un éxito. El llamador sigue sin recibir prueba transferible de sesión.
- Permisos SELECT/INSERT, sin UPDATE/DELETE/TRUNCATE para carrillo_app. Propietario
  de migraciones separado y fuera de esa garantía; no se declara WORM o protección
  contra SQL arbitrario capaz de insertar evidencia falsa.
- Downgrade rechazado ante eventos o factores, incluso sin consumos, para no volver
  a consumidores sin auditoría. Sin rutas globales de consulta/exportación para
  administradores de empresa; eventos de rechazo y demás auditoría pendientes.
- Siguiente 2.24: cuotas MFA compartidas entre propósitos, independientes de las
  de contraseña, con fallos persistentes y contención probados. MFA sigue sin
  activarse en autenticación, selección, reautenticación o recuperación.

## Acceso 2.24: cuotas MFA persistentes e independientes

- 0011 crea mfa_throttles separada de login_throttles. Cuenta ligada al UUID global
  del servidor, IP normalizada (IPv4 mapped comparte clave), hashes con dominios
  por scope. Ni empresa, correo, propósito, pendiente o flags del cliente eligen
  presupuesto/límites. Cinco fallos por cuenta, treinta por IP, quince minutos.
- MfaVerifier posee la transacción: confirma rechazos como fallos persistentes;
  acepta consumo/auditoría en el mismo commit. No sesión/pendiente/autorización
  por devolver resultado. Futura transición de autenticación debe integrarse en
  esa transacción, no confiar en un resultado entre transacciones.
- Orden IP -> cuenta -> usuario -> factor/hijos. UPSERT/locks/refresco evitan
  pérdidas de incrementos. Ventana desde primer fallo y renovación exacta al
  alcanzar quince minutos. Bloqueados no evalúan ni alargan ventana. No sleeps.
- Éxito del factor conserva **ambos** historiales: no equivale a autenticación
  completa. Es una elección conservadora dentro de la opción 2.20, sin reset.
  Contraseña exitosa y pendientes nuevos tampoco limpian contadores MFA.
- Errores de DB, protección de claves, escritura de auditoría y reloj fallan
  cerrados con mensaje genérico, sin cobrar código incorrecto o publicar éxito.
  App sin DELETE; downgrade rechaza cuotas/factores, incluso filas en cero.
- Contrato en mfa-verification-contract.md. Faltan retención, volumen/observabilidad,
  revisión/obligación, pendientes, guardias de sesión y UI/correo antes de activar
  MFA. Siguiente 2.25: estado global/revisión de seguridad del usuario.

## Acceso 2.25: revisión global

0012 añade estado neutral, trigger de revisión monotónica/obligación persistente
y eventos tipados de avance. El servicio autorizado por su llamador revoca todas
las sesiones/pendientes y audita en el mismo commit. No es un endpoint ni prueba
transferible. Sin hooks automáticos en roles/factores o login: integración pendiente.
Siguiente 2.26: pendientes tipados. Ver `deliveries/access-2.25.md`.

Acceso 2.26: separar pendientes MFA de la selección heredada evita conceder
permisos de negocio a un primer factor. Propósitos cerrados login_totp/enrollment;
5/15 minutos desde contraseña. Rotación conserva origen/plazo. Consumo devuelve
solo contexto restringido y debe componerse con la transición autorizada antes
de commit. Orden usuario -> selección heredada -> pendientes MFA -> sesiones.
Sin HTTP, recuperación ni habilitación MFA en esta entrega.

Acceso 2.27: verify_login_totp deriva identidad/factor del estado del servidor;
no admite IDs/proof flags del cliente. Un código rechazado no consume el pendiente.
Éxito rota a login_verified y persiste evidencia en la transacción propietaria,
sin renovar el plazo. El emisor tras contraseña no puede crear esa etapa.
Orden IP -> cuenta -> usuario -> pendiente -> factor; no adquirir tenant después.
Lectura inicial sin locks solo descubre identidad, luego se relee bajo locks.

Acceso 2.28: referenciar la prueba consumida evita copiar evidencia MFA a otra
sesión. FK y unicidad preservan una prueba/una sesión; servicios validan dueño,
propósito, revisión, contraseña, factor y tiempos. No backfill de sesiones:
revisión NULL exige login nuevo. Factor vigente o mfa_required bloquean la vía
heredada; obligación admin por rol espera hooks/incorporación completos. La
confirmación reciente MFA usa la prueba más antigua, sin renovación por selección
o actividad; endpoint de contraseña no la renueva. Sin activación pública.

Acceso 2.29: begin_mfa_login es una entrada interna separada del adaptador web
actual. Comparte verificadores/cuotas de contraseña; aplica política global y
conserva las cuotas MFA. No acepta tenant/rol/propósito. Incorporación pendiente
no prueba buzón ni permite fijar semilla. Historial de factor sin factor actual
no abre una incorporación por correo; requiere recuperación/sustitución aparte.
Se toma la hora al verificar contraseña y no se renueva al esperar otros locks.

Acceso 2.30: token de correo separado de pendiente y permiso de incorporación,
con hashes por propósito y destinatario derivado del correo exacto registrado.
Confirmar no equivale a activar TOTP o emitir sesión. Vencimiento total de quince
minutos desde contraseña; sin renovación. Lectura/inspección no consume. Permiso
ligado a fuente/navegador/revisión/contraseña/dirección, cerrado en revocaciones
globales o reinicio del mismo navegador. Un desafío por pendiente: sin recuperar
token ni reenvío implícito; requiere outbox cifrada/cuotas antes de envíos reales.

Contexto del titular: el SaaS pertenece al desarrollador; Taller Carrillo es una
empresa afiliada. Administrar Carrillo no concede listado de afiliadas ni datos
de otros negocios. El selector muestra solo membresías activas del usuario; con
una empresa se selecciona automáticamente. Administración del SaaS y del taller
son ámbitos separados; aún no se implementa una consola del propietario. El
inventario futuro debe filtrar y autorizar cada consulta/movimiento por la empresa
derivada de sesión en servidor, nunca un tenant o rol enviado por navegador.

## Acceso 2.31: staging de correo y cuotas independientes

Decisión técnica: máximo tres mensajes por cuenta y veinte por IP en una ventana
persistente de quince minutos; separación mínima por cuenta de
sesenta segundos. Son límites iniciales de seguridad, no reglas comerciales.
Reiniciar navegador/login o confirmar correo no reinicia las cuotas. Solo contar
mensajes encolados en el mismo commit del desafío; rechazos no gastan reservas.
El adaptador HTTP futuro debe usar la IP del proxy verificado, no campos del cliente.

AES-256-GCM con nonce aleatorio de 96 bits y AAD ligada a propósito/versión, usuario,
desafío, caducidad e identificador de clave. Claves de cifrado fuera de DB y separadas
de TOTP; clave HMAC de cuotas independiente y estable ante rotación de cifrado.
Sin claves por defecto ni carga/configuración productiva todavía. Sin claves de
correo no se emite desafío nuevo; lectura/confirmación no las necesitan.
La rotación futura debe preservar claves antiguas mientras haya mensajes vivos,
y cerrar una transición de cuotas si cambia la clave HMAC. No regenerar al arrancar.

Descartar ciphertext al confirmar/revocar y con purga explícita acotada al caducar;
no equivale a borrado seguro de backups/WAL. Falta programar mantenimiento.
Esta fase no implementa envío, leases/reintentos ni afirma entrega; sigue consumidor
y transporte simulado. No introducir SMTP/HTTP antes de esos controles.

## Acceso 2.32: entrega conservadora y prioridad de cierre

Continuar seguridad antes de inventario, conforme aclaró el usuario. Criterios
finitos y evidencia en access-security-closure.md; no medir avance solo por tests.

Consumidor interno con transporte inyectado, sin SMTP ni worker programado.
Lease máximo treinta segundos, nunca más allá de caducidad. Validar identidad,
revisión, contraseña, dirección, propósito, estado y token bajo locks; confirmar
lease y revalidación antes de llamar transporte fuera de la transacción.
Reintentar solo NOT_SENT explícito, con sesenta segundos y máximo tres intentos,
misma identidad del mensaje y sin gastar cuota nueva de solicitud. Unknown,
excepción, respuesta tardía o lease abandonado: incierto terminal, sin reenvío.
La recuperación conservadora puede perder un envío no comenzado si el proceso
muere; nunca se interpreta timeout como prueba de ausencia de efecto.

No hay exactamente-una-entrega entre PostgreSQL y un proveedor externo. La
revocación entre última validación y envío puede permitir que salga un correo
ya inválido: el consumo vuelve a exigir estado vigente, sin autoridad del envío.
Los futuros adaptadores deben imponer deadline total y usar origen HTTPS canónico;
no pueden mapear fallos genéricos a NOT_SENT ni registrar el payload.

## Acceso 2.33: incorporación TOTP inicial

Intercambiar permiso de buzón una vez por setup cifrado, ligado al mismo navegador
y al plazo de quince minutos desde contraseña. No revelar semilla de nuevo; si
se pierde la respuesta, reiniciar el flujo explícitamente. La autoridad del setup
no es la posesión de un ID: al activar se releen identidad, revisión, contraseña,
correo exacto, prueba original y ausencia de todo historial de factor.

Usar perfil/clave TOTP existentes; AAD usuario/UUID reservado del futuro factor.
Reencriptar con la clave activa al confirmar y retirar ciphertext del setup.
Compartir cuota MFA global IP/cuenta antes de locks de identidad. Código incorrecto
gasta cuota, éxito no la reinicia. Consumir primer OTP, crear factor y diez hashes
offline, avanzar revisión/revocar sesiones/pendientes y auditar en un commit.
Sin sesión automática ni relectura de códigos. Pérdida de la respuesta tras commit
requiere login MFA y futura regeneración segura, no replay de setup.

No se añade marca User.email_verified ni backfill. La evidencia persistida de
buzón está vinculada al valor exacto vigente durante la incorporación; su uso
para elegibilidad de reset de contraseña debe integrarse explícitamente.
Cierre/purga de setups conserva metadatos y retira payload; backups/WAL tienen
retención propia, no se afirma borrado seguro de memoria o almacenamiento.

## Acceso 2.34: inicio offline de recuperación

Reutilizar pendiente LOGIN_TOTP ya ligado a contraseña/navegador, nunca aceptar
IDs de usuario/factor/conjunto del cliente. Cuotas MFA IP/cuenta preceden locks
de identidad y consumo; formato incorrecto también gasta fallos si el pendiente
es válido. Un código consumido, revocación global y permiso nuevo se confirman
juntos. No revocar factor/conjunto ni aumentar revisión al iniciar: ocurre al
confirmar el reemplazo, que sigue pendiente. El permiso no abre negocio.

Caducidad del permiso: quince minutos desde emisión, conforme contrato; exigir
contraseña dentro de sus cinco minutos antes de emitir. No renovar por inspección
o consumo; plazo puede superar al pendiente de contraseña original, nunca omitir
comprobación de huella/revisión actual. Consumo de permiso solo como composición
privada para futuro setup, con rollback de cualquier trabajo posterior fallido.

Cancelar al confirmar una sesión normal con evidencia MFA vigente, en la guardia
central de creación y misma transacción; TOTP aislado no cancela. También cerrar
por revocación global y reinicio del mismo navegador. No devolver códigos consumidos
por cancelación o pérdida de respuesta. Un código distinto y contraseña fresca
permiten reiniciar, dentro de cuotas. No publicar HTTP antes de reemplazo/recuperación
completos. Avisos de seguridad/correo, retención y operación siguen pendientes.

## Acceso 2.35: sustitución por recuperación offline

Intercambio de permiso por setup de semilla cifrada y token con propósito propio;
no ampliar quince minutos de emisión ni reutilizar semilla por relectura. No
aceptar ID de usuario/factor/permiso como prueba externa: el acceso por ID es
solo un detalle de la revalidación detrás del token de setup y navegador.

Mantener factor/conjunto anterior activos hasta confirmar TOTP nuevo. Reencriptar
con clave activa, retirar viejo factor/conjunto, crear sucesor y diez hashes,
consumir primer OTP, avanzar revisión/revocar y auditar en un commit. No sesión
automática; código del alta ya está usado para login. Quotas compartidas sin reset.

Cierre de permiso por sesión normal/reinicio/revocación también retira ciphertext
del setup; purga explícita para caducados. No devuelve código usado. Si se pierde
la respuesta de setup, reiniciar con contraseña y otro código offline. Si se
pierde la respuesta de códigos después de reemplazar, exigir MFA del factor nuevo
y futura regeneración segura, nunca replay. No implementar rotación voluntaria
como si fuera recuperación; esta entrega exige el permiso offline específico.

## 2.36 — Regeneración con evidencia combinada reciente

Se reutiliza el login MFA completo dentro de cinco minutos desde la prueba más antigua, conforme al contrato; no basta contraseña confirmada o sesión antigua. Operación propia global, independiente del rol. Regenerar conserva factor/contador, invalida el conjunto anterior, avanza revisión y cierra todas las sesiones para impedir replay/concurrencia. Sin cuota adicional por propósito porque no evalúa secretos nuevos; login conserva sus cuotas. Nueva evidencia de reautenticación y adaptación HTTP/UI pendientes.

## 2.37 — Evidencia explícita del buzón

Guardar evidencia histórica al confirmar contraseña/navegador/token de incorporación, sin backfill. No equiparar evidencia del buzón con permiso de reset o segundo factor; cada propósito tendrá token propio. Cambio de contraseña/revisión no borra evidencia; cambio de dirección sí la invalida mediante trigger en la misma transacción y no revive por revertir correo. No exigir membresía activa para consultar identidad global, pero nunca reactivarla ni emitir sesión por esta consulta. Verificación general para cuentas sin MFA pendiente.
