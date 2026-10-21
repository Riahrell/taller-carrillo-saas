# Revisión del flujo de productos e imágenes

Se revisaron las pantallas de catálogo, alta, inventario y carga de imágenes,
sus clientes/API y la configuración local. Esto no es una auditoría completa
del SaaS ni una prueba funcional de todas sus operaciones.

## Hallazgos y cambios

El alta de producto incluye nombre, precio, SKU y categoría, pero no un archivo
de imagen. La carga estaba únicamente en la ficha de Inventario. El catálogo
mostraba miniaturas sin un acceso a administrar esa foto. La afirmación anterior
de que el formulario de alta ya incluía la foto fue imprecisa.

Ahora cada fila de producto ofrece «Ver inventario e imagen», que abre la ficha
del producto correspondiente. Tras un alta confirmada aparece «Agregar imagen al
producto creado». El formulario explica que la foto se agrega después de guardar.
La ficha conserva la carga existente con galería/cámara, revisión y confirmación.

Inventario acepta ?producto=<UUID> como candidato, sin autoridad sobre empresa ni
permisos: consulta detalle, saldo e historial con la membresía autenticada.
Rechaza selección mal formada o de otra empresa; nunca sustituye silenciosamente
un producto solicitado inválido por el primero. Un conteo/imagen pendiente tiene
prioridad para conservar la operación original.

Cuando falla la lectura de imagen, la ficha permite «Volver a consultar imagen»
en vez de dejar únicamente el botón de carga deshabilitado.

El costo calculado y las existencias proceden de movimientos y se muestran en
Inventario. No se inventó un costo inicial dentro del alta de catálogo.
Compras tiene API pero sigue sin interfaz. El escaparate público tampoco forma
parte del catálogo privado actual. No se declara el sistema entero terminado.

## Archivos completos cambiados

- frontend/src/components/catalog-manager.tsx
- frontend/src/components/inventory-manager.tsx
- frontend/src/components/product-image.tsx
- este documento

## Comandos y resultados reales

Desde la raíz:
```bash
cd frontend
NODE=/home/riahrell/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/eslint/bin/eslint.js .
cd ..
git diff --check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build frontend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait frontend
```

Formato, TypeScript y ESLint correctos. Compilación registrada en
.local/validation/catalog-image-links-build.log. La configuración privada de S3
se cargó dentro del backend sin imprimir secretos ni efectuar operaciones S3.
La carga correcta de configuración no demuestra conectividad o una subida.
No se ejecutaron pruebas funcionales, se crearon productos ni se subieron fotos
desde el agente, conforme a la preferencia del propietario.

## Prueba manual pendiente

1. Recargar http://localhost:8080 e iniciar sesión.
2. Catálogo → Nuevo producto → confirmar identidad → completar datos →
   revisar y confirmar alta. Comprobar nombre/precio/SKU/categoría.
3. Pulsar «Agregar imagen al producto creado»: comprobar que abre exactamente
   ese producto en Inventario, incluso si no está en la primera página.
4. «Agregar imagen» → confirmar identidad → elegir galería o cámara →
   revisar imagen → confirmar. Se admite JPEG, PNG o WebP estático hasta 8 MiB.
5. Volver a Catálogo: debe verse su miniatura. Abrir «Ver inventario e imagen»
   desde esa misma fila: verificar el producto y probar «Cambiar imagen».
6. En celular, comprobar ambas opciones: cámara y galería. El navegador y los
   permisos del teléfono determinan si se abre la cámara; no se validó un móvil
   físico. HEIC no está admitido.
7. Si aparece «Las imágenes no están disponibles», probar «Volver a consultar
   imagen» y comunicar solo el mensaje, nunca claves ni archivos de configuración.

El alta y la imagen son operaciones separadas: si falla la foto, el producto
permanece creado y no hay que duplicarlo. Las imágenes siguen privadas.
Siguiente tarea: atender el resultado manual y continuar los módulos pendientes,
sin presentar la API de compras como una pantalla ya disponible.
