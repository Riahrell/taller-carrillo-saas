# Estado y versiones

Revisión documental: **3 de noviembre de 2026**. Esta página distingue el código
consultado en GitHub del último despliegue comprobado durante el proyecto.
No representa una comprobación de salud en vivo de la VPS.

## Qué versión estoy leyendo

| Referencia                                       | Estado comprobado                                                                                                                                                             |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `origin/main`, antes de esta revisión documental | `5cc7958`: acceso, empleados, catálogo privado, inventario, API de compras y variantes de fotografías; migraciones hasta `0047_product_image_variants`.                       |
| Código de aplicación local posterior             | Añade eliminación controlada de productos, fichas publicables, catálogo anónimo, sitio comercial y correcciones de interfaz/proxy; migraciones hasta `0049_catalog_listings`. |
| Último despliegue registrado, 25 de octubre      | Código `66c83e6`: sitio y panel separados, catálogo público y corrección de compatibilidad con Cloudflare. El propietario confirmó que volvió a poder abrir la edición.       |

Entre `5cc7958` y la revisión local anterior a esta documentación había 40 commits.
Publicar estas guías no incorpora esos cambios de aplicación a `main`.

Para verificar tu copia sin mostrar configuración privada:

```bash
git log -1 --oneline
git status --short
```

Consulta la revisión real de Alembic con el comando de la
[guía de desarrollo](development.md#consultar-la-revisión-de-la-base).
No deduzcas el esquema a partir del nombre de una entrega.

## Qué está implementado

| Función                                                    | En `main` revisado | En la V1 operativa registrada |
| ---------------------------------------------------------- | ------------------ | ----------------------------- |
| Acceso y recuperación de contraseña                        | Sí                 | Sí                            |
| Registro/vinculación y permisos del personal               | Sí                 | Sí                            |
| Productos, categorías, búsqueda y fotografías              | Sí                 | Sí                            |
| Entradas, salidas manuales, conteos, historial y mínimos   | Sí                 | Sí                            |
| API de proveedores/compras y recepción                     | Sí                 | Sí                            |
| Eliminación administrativa con restricciones               | No                 | Sí                            |
| Fichas publicables y catálogo sin registro                 | No                 | Sí                            |
| Sitio Inicio/Servicios/Precios/Contacto                    | No                 | Sí                            |
| Protección de versiones HTTP ante transformaciones del CDN | No                 | Sí                            |
| Pantalla completa de compras, ventas, cobros y órdenes     | Pendiente          | Pendiente                     |

Los enlaces públicos registrados son [sitio](https://taller-carrillo.com/sitio)
y [catálogo](https://taller-carrillo.com/vitrina). El panel exige cuenta y permisos;
no se ofrecen credenciales de demostración en el repositorio.

## Pendientes que deben permanecer visibles

- Integrar y validar en GitHub los cambios de aplicación posteriores a `5cc7958`.
- Revisar la excepción de dependencias `SEC-2026-001`: el archivo versionado
  indica vencimiento el **31 de octubre de 2026**. Esa fecha ya pasó; esta revisión
  documental no la renueva ni demuestra que las dependencias estén corregidas.
- Validar compras con su interfaz futura y la salida ligada a una venta u orden
  real. Una salida manual no sustituye esos documentos.
- Mantener pruebas de restauración, revisión de parches y seguimiento operativo.
  El parche de kernel se había aplazado; su estado actual no se verificó aquí.
- Revisar indexación y contenido público antes de tratar el sitio como una
  entrega comercial definitiva. Publicar productos sigue siendo decisión del administrador.

## Cómo interpretar las pruebas

El historial registra validaciones de versiones concretas. Por ejemplo, para la
corrección del 25 de octubre pasaron 91 pruebas frontend y 11 integraciones por
Nginx. Esos números no son el total actual del proyecto ni acreditan un CI nuevo.
Consulta [Pruebas y CI](testing.md) para reproducir la validación de tu revisión.
