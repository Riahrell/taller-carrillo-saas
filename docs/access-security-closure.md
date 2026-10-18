# Cierre de seguridad de acceso antes del inventario

Prioridad confirmada por el usuario: terminar esta parte de seguridad primero.
Este documento organiza el alcance ya acordado en MFA/recuperación, revisión de
seguridad y staging; no añade certificaciones ni módulos nuevos. No equivale a
«seguridad terminada» porque pasen pruebas de componentes internos.

| Bloque | Criterio de cierre comprobable | Estado al 2.36 |
| --- | --- | --- |
| Correo transaccional | Persistencia cifrada/cuotas, consumidor con revalidación, fallos/reintentos; adaptador real con deadline, remitente verificado y entrega probada sin filtrar secretos | Cola y consumidor internos; transporte solo simulado, proveedor pendiente |
| Incorporación y login MFA | Prueba fresca de buzón, primer TOTP activado atómicamente, códigos offline entregados una vez; login y selección exigen evidencia vigente | Incorporación, activación/códigos y login internos; HTTP/UI y claves operativas pendientes |
| Ciclo de identidad y recuperación | Correo verificado explícito, reset de contraseña sin desactivar MFA; códigos offline, reemplazo de factor y último administrador sin bypass; cambios de rol/factor y reautenticación coherentes | Entrada/reemplazo offline internos 2.34–2.35; regeneración interna 2.36; reset/hook pendientes |
| Integración HTTP e interfaz | Recorridos completos en navegador, CSRF/origen/cookies/proxy, ningún GET consume pruebas; contraseñas/TOTP/códigos fuera de URL/log/almacenamiento persistente y enlaces de correo sin filtraciones; aislamiento de empresas en cada operación | Login actual funciona; integración MFA/recuperación pendiente |
| Operación y revisión final | HTTPS, claves externas/rotación, cuotas/purga/retención programadas, restauración real de copia, auditoría y regresión de seguridad; hallazgos resueltos o excepción vigente revisada según alcance | Desarrollo local; staging público y aceptación operacional pendientes |

Cada entrega debe indicar qué criterio avanza y qué prueba lo demuestra. El
bloque de incorporación avanzó con activación/códigos atómicos en 2.33. El
inicio de recuperación offline quedó implementado en 2.34, sin sesión de negocio.
2.35 confirmó el reemplazo/códigos de manera atómica. 2.36 añade regeneración
interna con prueba combinada reciente del login y cierre de sesiones. El siguiente
trabajo es **cerrar la elegibilidad de correo para reset de contraseña**, sin
desactivar el factor ni invalidar códigos MFA sin usar. No publicar
HTTP antes del flujo completo. El proveedor/DNS se concretará al integrar el adaptador real;
no pedir credenciales por chat ni contratar servicios por este documento.

No prometer fecha o cantidad fija de entregas sin cerrar la integración. No
ampliar el alcance con mecanismos opcionales salvo un defecto, requisito del plan
o decisión explícita; documentar el motivo. La cantidad de tests no mide por sí
sola el avance. Inventario empieza después de verificar estos criterios, conforme
a la prioridad del usuario. Cada módulo de negocio conservará sus propias pruebas
de autorización, aislamiento y consistencia de stock/finanzas.
