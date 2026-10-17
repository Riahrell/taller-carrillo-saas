# Pruebas remotas: Contabo y dominio pendiente

Contexto confirmado por el usuario el 2026-10-16: dispone de un VPS en Contabo
que también aloja imágenes; prevé comprar el dominio en Namecheap. No se ha
facilitado/registrado un nombre de dominio ni inspeccionado el VPS desde aquí.
No se ha accedido por SSH, cambiado DNS, comprado servicios o publicado la app.
No enviar credenciales del VPS/DB/DNS por chat ni incorporarlas al repositorio.

## Preparación antes de cambiar el VPS

1. Inventario de solo lectura de proxy/webserver, contenedores, puertos ocupados,
   rutas/volúmenes de imágenes y bases, recursos disponibles y backups. No basta
   con saber el proveedor para suponer una configuración o capacidad.
2. Identificar el proxy que ya usa 80/443 y diseñar el virtual host del subdominio.
   Evitar instalar otro proxy sobre esos puertos sin revisar el servicio existente.
3. Proyecto y secretos independientes para Carrillo staging; PostgreSQL propio
   en red privada, sin reutilizar la base/volúmenes de imágenes o negocio. Usar
   datos de prueba, cuentas individuales y acceso remoto restringido mientras
   las brechas de `security-review.md` sigan abiertas.
4. Preparar archivos y comandos revisables antes de cambiar DNS o ejecutar el
   despliegue. La autorización para preparar este entorno no autoriza borrar
   contenedores/volúmenes ajenos o ejecutar prune/global cleanup en el VPS.

## Dominio y HTTPS

Comprar el nombre no habilita TLS automáticamente. Cuando el usuario confirme
el dominio, elegir un subdominio de pruebas, configurar su DNS al VPS y emitir
un certificado válido con renovación automatizada. La integración concreta
depende del proxy existente; no se ha decidido sustituirlo.

Validar origen canónico HTTPS, redirección HTTP->HTTPS, certificado/cadena,
renovación, cookies Secure/HttpOnly/SameSite, CSRF/origen, ausencia de contenido
mixto y cierre de puertos internos. HSTS solo después de comprobar HTTPS y el
alcance de subdominios. No aceptar avisos de certificado para eludir estas pruebas.

Se requieren acceso administrado al servidor y configuración DNS, mediante canal
seguro y permisos mínimos. Son pendientes operativos; todavía no existe evidencia
de despliegue TLS. El HTTP de localhost actual es exclusivamente desarrollo.

## Aceptación del entorno remoto

- El servicio de imágenes anterior sigue funcionando y sus datos permanecen.
- DB/secretos/redes/volúmenes de staging son independientes y los tests no apuntan
  a recursos operativos. No hay carga de datos reales en staging por defecto.
- Backup privado y restauración aislada ensayados antes de cambios irreversibles.
- Acceso remoto restringido hasta cerrar brechas prioritarias; no basta un dominio
  o certificado para afirmar que la aplicación es segura o apta para producción.
- Smoke HTTPS, permisos entre empresas, rutas sensibles, logs sin secretos y
  recuperación del despliegue comprobados con resultados reales documentados.

Siguiente preparación remota: inventario de solo lectura del VPS y elección de
subdominio después de adquirir el nombre. No hay contraseñas, IPs públicas o
credenciales predeterminadas codificadas en este documento.
