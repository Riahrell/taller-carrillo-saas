# Mínimo de inventario por producto

Primera entrega de stock bajo: configuración y aviso en la ficha del producto.
El listado global se incorporó después en la entrega 3.38; su contrato está en
`docs/low-stock-list-contract.md`. No se generan compras, movimientos, correos ni pedidos automáticos.

## Regla explícita

- Sin configurar (`null`): no avisar. No se asigna ningún valor a productos existentes.
- Mínimo configurado: avisar cuando las existencias sean menores o iguales al mínimo.
- Cero: avisar al quedar sin existencias.
- Producto inactivo: conserva su configuración pero suspende el aviso.
- Cantidades exactas, no negativas, con hasta tres decimales y límites NUMERIC(18,3).

El texto del formulario explica el límite inclusivo antes de confirmar. La
advertencia muestra la cantidad consultada, no un cálculo de stock del cliente.

## HTTP privado

`GET /inventory/products/{product_id}/minimum`: administrador vigente, misma
comprobación de lectura/navegador/contexto que los demás GET de inventario.
Devuelve exclusivamente `product_id`, `minimum`, `quantity`, `is_active`, `is_low`;
cantidades como cadenas con tres decimales. Cabeceras `Cache-Control: no-store`,
`X-Access-Membership`, `ETag: "stock-minimum-v1-UUID-REVISION"`.

`PATCH` en la misma ruta recibe únicamente `{"minimum": "2.000"}` o
`{"minimum": null}`. Requiere administrador, identidad confirmada vigente,
Origin/CSRF, contexto esperado e If-Match de la configuración revisada. Se valida
de nuevo el modelo incluso para llamadas internas. Nunca acepta stock, actor,
tenant, precio o rol del cliente. Versión antigua: 412; sin versión/contexto: 428;
precondición mal formada: 400; entrada inválida: 422; acceso ajeno: 403.

## Persistencia

Migración 0041: `stock_minimum_records`, evidencia append-only de configuración
con empresa, producto, actor, revisión, mínimo anterior/nuevo y fecha. Se consulta
el registro de mayor revisión; si no existe, mínimo nulo y revisión cero. El índice
único empresa/producto/revisión permite esa lectura. No hay tabla materializada
adicional ni duplicación del saldo.

Empresa/sesión/producto se bloquean con el protocolo existente. Se compara la
versión, se inserta un registro solo si cambió el mínimo y se revalida autorización
antes del commit. El cambio y su evidencia son el mismo registro. No se escriben
tablas de stock ni finanzas. Guardar lo mismo no crea otra revisión. Un resultado
incierto no se reintenta automáticamente: se consulta la configuración antes de
repetir. La revisión evita que una configuración antigua sobrescriba otra nueva.

El runtime no puede UPDATE/DELETE/TRUNCATE la evidencia. La bajada se rechaza si
hay registros, incluso si el aviso se desactivó posteriormente. Los filtros de
empresa y FK compuestas impiden configurar productos de otra empresa.

## Interfaz y límites

En Inventario: «Configurar mínimo», confirmación de identidad, edición, revisión
y guardado. Vacío desactiva; no equivale a cero. Mientras se edita, quedan bloqueados
movimientos, fotos, selección y paginación del producto. La respuesta se valida con
BigInt y se comprueba la sesión antes de anunciar éxito. El aviso se renueva al
cambiar la cantidad del producto o al recargar la ficha. No es una suscripción en
tiempo real; cambios hechos en otra pestaña pueden requerir recargar.

Pendiente: revisión visual del propietario en móvil.
