# Documentación

Empieza por la guía que corresponda a tu tarea. Los contratos describen decisiones
técnicas; las entregas numeradas conservan lo que se comprobó en cada fase.

## Guías actuales

| Documento                                           | Para qué sirve                                                         |
| --------------------------------------------------- | ---------------------------------------------------------------------- |
| [Estado y versiones](project-status.md)             | Distinguir lo publicado en GitHub de la última instalación registrada. |
| [Desarrollo local](development.md)                  | Preparar el entorno, arrancar, migrar y detener conservando datos.     |
| [Guía de uso](user-guide.md)                        | Trabajar con productos, inventario, empleados y publicación.           |
| [Arquitectura](architecture.md)                     | Entender servicios, límites de acceso y consistencia de datos.         |
| [Pruebas y CI](testing.md)                          | Ejecutar validaciones y entender fallos o cancelaciones de Actions.    |
| [Operación de producción](production-operations.md) | Diagnóstico, actualizaciones, copias, HTTPS y Cloudflare.              |
| [Ficha para CV](portfolio.md)                       | Resumen del proyecto y puntos técnicos para una entrevista.            |

## Contratos para trabajar en el código

| Tema                     | Referencias                                                                                                                                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Acceso y empleados       | [HTTP de acceso](auth-http-contract.md), [membresías](membership-http-contract.md), [registro](employee-registration-contract.md), [MFA y recuperación](mfa-recovery-contract.md).    |
| Catálogo interno         | [Catálogo](catalog-contract.md), [API](catalog-http-contract.md), [edición de productos](product-editing-contract.md).                                                                |
| Existencias              | [Conteos](inventory-count-contract.md), [movimientos manuales](inventory-manual-contract.md), [historial](inventory-history-contract.md), [reversas](inventory-reversal-contract.md). |
| Permisos de inventario   | [Consulta](inventory-read-permissions-contract.md), [salidas](inventory-withdrawal-permissions-contract.md).                                                                          |
| Fotografías              | [Imágenes privadas](product-images-contract.md), [variantes](product-image-variants-contract.md).                                                                                     |
| Compras                  | [Modelo y reglas](purchases-contract.md), [API](purchases-http-contract.md).                                                                                                          |
| Seguridad y contenedores | [Escaneos](security-scanning.md), [imágenes y SBOM](container-security.md).                                                                                                           |

Un contrato puede incluir notas sobre una fase anterior. Contrasta su alcance con
la rama que estás leyendo y con las rutas y pruebas de esa revisión.

## Historial y planificación

- [Entregas](deliveries/): evidencia por fase; no son instrucciones de despliegue actual.
- [Plan Maestro revisado](reviews/plan-maestro-v1.1.md): referencia de requisitos.
- [Plan de inventario](inventory-release-plan.md): alcance y evolución del módulo.
- [Decisiones](decisions.md) y [modelo inicial](data-model.md): contexto del diseño.

El historial anterior del README se conserva en Git. Para operar el sistema,
utiliza las guías anteriores y comprueba primero la revisión instalada.
