# Registro de empleados y autorización

Decisión del propietario, 21 de octubre de 2026: el empleado se registra y el
administrador de su empresa autoriza si puede modificar inventario.

## Comportamiento acordado

- Registrarse no concede acceso a empresas ni permisos de inventario.
- La cuenta verifica su correo mediante el flujo vigente de códigos de seis caracteres.
- El administrador aprueba la vinculación del empleado a su propia empresa.
- El administrador concede explícitamente los permisos para modificar inventario.
- El servidor resuelve empresa, membresía y permisos en cada operación.
- El empleado no puede elegir un rol privilegiado, aprobarse ni consultar otras empresas.
- Revocar permisos debe impedir la siguiente escritura, incluso con una sesión abierta.

## Diseño propuesto, pendiente de implementación

Usar una invitación o referencia opaca para dirigir la solicitud a una empresa,
sin publicar un listado de empresas afiliadas. Mantener separadas la identidad,
la solicitud de incorporación y la membresía aprobada. La aprobación y los
cambios de permisos deben quedar auditados y limitarse a administradores de esa
empresa. No conceder creación de usuarios administradores mediante registro público.

Antes de publicar el registro se definirán en el contrato los permisos concretos
sobre productos, imágenes y movimientos; todos denegados por defecto. La UI
mostrará claramente el estado pendiente y las acciones autorizadas.

## Aceptación

1. El registro por sí solo no permite leer ni modificar inventario de una empresa.
2. Un administrador no puede aprobar solicitudes para otra empresa.
3. Un empleado aprobado sin permiso de escritura no puede modificar existencias,
   productos o fotos mediante peticiones directas al backend.
4. Con permisos concedidos solo puede realizar las operaciones autorizadas.
5. La revocación se aplica sin esperar un nuevo inicio de sesión.
6. La cuenta no enumera empresas ni se concede permisos a sí misma.

Estado: decisión documentada; el registro autónomo todavía no está implementado.
La reparación de la CI en curso precede esta entrega.
