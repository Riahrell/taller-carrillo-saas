# Lista privada de productos por reponer

`GET /inventory/low-stock?limit=25&after=UUID`: administrador vigente, sesión y
lectura de navegador verificadas, tenant del servidor. Cuerpo `items`, `next_after`;
cada fila contiene `id`, `name`, `sku`, `quantity`, `minimum`. Cantidades exactas
con tres decimales. Respuesta no-store y cabecera ligada a la membresía lectora.

Se usa la mayor revisión del mínimo de cada producto, no un mínimo histórico.
Solo productos activos, mínimo no nulo y cantidad <= mínimo. Sin saldo materializado
se considera cero. Nunca aparecen productos sin configuración, desactivados,
por encima del mínimo o de otra empresa. La regla ya fue definida al configurar
el mínimo; esta consulta no crea reglas ni valores nuevos.

Orden UUID ascendente, consulta de una fila adicional para paginar. El cursor
se verifica dentro de la empresa, aunque el producto haya dejado de necesitar
reposición. Parámetros desconocidos o duplicados se rechazan. El conjunto puede
cambiar entre consultas: no es una instantánea congelada ni una suscripción.

Interfaz desplegable a demanda dentro de Inventario. Muestra fotos privadas con
el componente existente, existencias y mínimo; permite abrir el producto, actualizar
avisos y paginar. Lista independiente de los filtros del buscador. Las consultas
no desmontan la lista anterior durante la carga. No registra compras ni envía correos.

Sin migraciones ni cambios de dependencias. Se corrigió además el bloqueo de
«Actualizar consulta» mientras se edita un mínimo o una foto.
