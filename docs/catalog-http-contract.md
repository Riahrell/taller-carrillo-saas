# Catálogo administrativo — HTTP 3.4

Decisión técnica para conectar los casos de catálogo del plan, sin atribuir estas
URL al PDF. Mismo origen del panel; privadas, sin publicación ni ventas. La API
del proxy antepone `/api`; FastAPI recibe `/catalog/...` internamente.

| Método y ruta pública | Entrada | Resultado |
| --- | --- | --- |
| GET `/api/catalog/products` | limit y after opcionales | Página de productos administrativos. |
| GET `/api/catalog/categories` | limit y after opcionales | Página de categorías administrativas. |
| GET `/api/catalog/products/{product_id}` | UUID de producto | Detalle de la empresa autorizada. |
| POST `/api/catalog/products` | ProductCreation existente | 201, producto confirmado y auditado. |
| POST `/api/catalog/categories` | CategoryCreation existente | 201, categoría confirmada y auditada. |

## Autoridad y transporte

- Empresa/rol/actor se resuelven desde la cookie de sesión actual, con usuario,
  membresía, factor/revisión y empresa vigentes. Sesión no se selecciona mediante
  query, cuerpo ni cabeceras de contexto.
- Rutas administrativas: solo admin. No habilitan todavía consulta operativa de
  cajero, mecánico o solo lectura. Esa autorización se conectará en sus casos de
  uso, con las restricciones de `permissions.md`.
- Todas verifican peer del proxy y X-Real-IP mediante el resolvedor existente.
  No confiar en cabeceras de un cliente conectado directamente al backend.
- GET exige `X-Requested-With: XMLHttpRequest` y política existente de Origin/
  Sec-Fetch-Site. X-Expected-Membership opcional compara contexto, no da autoridad.
- POST exige Origin canónico, CSRF ligado a sesión, JSON limitado a 8192 bytes,
  sin claves duplicadas; X-Expected-Membership obligatorio y UUID canónico.
  Sesión admin con confirmación reciente dentro de la transacción, revalidada
  antes del commit. CSRF no renueva esa prueba.
- Cookies pendientes de selección/login no sustituyen la sesión. La combinación
  de cookies de preautenticación/selección con una de sesión se rechaza.
- Respuestas no-store, sin nuevas cookies, con X-Access-Membership del servidor
  en éxitos para que el cliente descarte resultados de otro contexto.
- Nginx aplica límites compartidos de peticiones/conexiones, cuerpo y tiempo;
  normaliza 413/429/503, sin logs de errores de petición que incluyan cabeceras
  o query. Acceso registra URI sin argumentos. TLS se genera desde la misma
  plantilla; no implica despliegue/certificado público verificado.

## Representaciones y páginas

Categoría: id, name, is_active. Producto: id, name, is_active, category_id, sku,
price. Precio se serializa como string Decimal; no contiene tenant, identidad
global, actor, costo, stock o secretos. No reutilizar estas representaciones como
contrato público. Campos de alta y límites en `catalog-contract.md`.

Página: items y next_after (UUID o null). limit por defecto 25, rango 1–100;
frontera after exclusiva, orden UUID. Consulta como máximo limit + 1 filas de la
empresa de sesión. No hay contador total, carga de todo el catálogo ni consulta
de la entidad cuyo UUID se usó como cursor. Cursor ajeno/desconocido no revela
su existencia ni cambia empresa. Páginas vivas: no son snapshot transaccional
entre peticiones. Administrador puede consultar registros inactivos para gestión.

Parámetros repetidos/desconocidos y límites ambiguos se rechazan. POST y detalle
no admiten query. No hay filtros, edición, eliminación, ETag o promesa de precio
para cobro en esta entrega. Consulta no crea eventos comerciales; actividad de
sesión se confirma en la transacción de lectura. Se devuelven DTO tras commit.

## Errores y reintentos

- 401: sesión ausente/inválida/revocada/vencida.
- 403: permiso/contexto/referencia denegados, origen/CSRF/peer/lectura no admitidos.
  Producto ajeno e inexistente comparten detalle `Access denied`, sin datos.
- 428: contexto obligatorio ausente o confirmación reciente requerida.
- 409: SKU no disponible en esta empresa, sin modificar producto existente.
- 422: contrato/query/UUID inválidos, sin reflejar datos enviados.
- 400/415/413: JSON ambiguo o inválido, tipo no permitido, cuerpo excesivo.
- 429: límites del proxy. 503: catálogo no disponible, sin error DB en respuesta.

No repetir automáticamente POST cuya respuesta se perdió: no hay idempotencia
general de altas. Consultar el catálogo y revisar estado antes de decidir otra
operación. Fallo de transacción conserva catálogo y auditoría sin efectos parciales.
