# Auditoría actual de acceso — 2026-10-18

Revisión de cierre contra el objetivo de controles de acceso previos al inventario,
con código `9e58e2f` y evidencia conservada. **El cierre operativo completo no está
demostrado.** El usuario autorizó continuar inventario local aplazando VPS; no se
interpreta esa decisión como HTTPS, respaldo remoto o aceptación externa realizados.

Las matrices de 2.38 y el seguimiento cronológico conservan estados históricos;
no significan que debamos volver a implementar MFA/reset que ya existen.

| Requisito | Evidencia inspeccionada | Conclusión y límite |
| --- | --- | --- |
| Correo cifrado, cuotas, despacho y fallos seguros | `enrollment_email.py`, `smtp_transport.py`, pruebas de consumidor en `test_enrollment_email.py`; CI actual incluye esas pruebas | Implementado/probado en local. Usuario confirmó recepción de dos correos de transporte Resend; no prueba recorridos operativos completos por HTTPS |
| Incorporación y login MFA | `test_identity_http.py` comprueba acceso restringido sin factor, TOTP y rechazo de repetición; entregas 2.39 y 3.11 | API/UI integradas; navegador real de 3.11 verificó contraseña, TOTP y selección. Activación/recuperación completas desde teléfono siguen pendientes |
| Reset sin retirar MFA | `password_reset.py`, `test_identity_completion.py`: un ganador concurrente, contraseña/revisión, aviso y evento; rollback por fallo/vencimiento | Implementado y probado en PostgreSQL; no equivale a reset manual real por enlace de Resend |
| Buzón, recuperación offline, reemplazo y códigos | `test_identity_http.py`, pruebas de incorporación/consumo/reemplazo y entrega 2.39, incluidas en CI actual | Integrados/probados por capas. No afirmar aceptación manual completa de todos los recorridos |
| Contexto, roles y revocación global | `test_security_state.py`, guardias de administración y pruebas recientes; 3.9–3.11 | Roles/empresa del servidor y revocación global probados; cada módulo conserva sus pruebas. No hay RLS contra SQL arbitrario del runtime |
| Reautenticación y vida absoluta | `test_identity_http.py` y `test_recent_administration.py`, regresión actual | Contraseña/TOTP, rotación y vencimiento protegidos; navegador 3.11 confirmó identidad antes de escribir stock |
| HTTP, navegador, secretos y aislamiento | Middleware de origen/CSRF/JSON, proxy, tests HTTP/componentes y QA 3.11 | Probado en entorno efímero. No es aceptación HTTPS pública ni prueba contra equipo/navegador comprometido |
| Worker, claves, mantenimiento y recifrado | `app/tools/identity_worker.py`, `rewrap_identity.py`, `access/maintenance.py`, entrega 2.39 y regresión | Implementación/validación local; programación y operación reales en Contabo no verificadas |
| Restauración de la aplicación nueva | Log `hardened-restore.log`: restore completo, descifrado y cuarentena con MFA | Prueba aislada con datos sintéticos aprobada; no demuestra recuperar el despliegue antiguo de Contabo |
| TLS | Log `hardened-tls.log`: sintaxis y archivos legibles por UID 101 | Certificado temporal de prueba; cadena pública y renovación sin verificar |
| Secretos, dependencias y contenedores | CI 3.11, reportes/SBOM privados y excepción vigente | Secretos/locks Python y cuatro runtime sin hallazgos; cuatro avisos npm dev-only aceptados hasta 2026-10-31, no resueltos |

## Evidencia vigente y reproducibilidad

Se inspeccionó `.local/validation/inventory-3.11-ci.log`: código 0, 1.595 pruebas
backend, 229 frontend, 9 por Nginx real; lint/formato/tipos/builds/smoke y escaneos
aprobados. Esos números no sustituyen la cobertura descrita en la tabla.
QA real de stock/contexto en `deliveries/inventory-3.11.md`, capturas sintéticas
y log de fixture finalizado con limpieza. Reportes/SBOM:
`.local/image-reports/bfe463fad9af48a291150a8e75753eb2/`; reporte de código/locks
`.local/security-reports/bdde3b9c5ed04c61b68b2868d4383e87.json`.

Inspeccionados también `.local/validation/hardened-restore.log` y
`hardened-tls.log`: ambos terminan aprobados con sus límites expresos. No se
repitieron restauración/TLS ni CI por esta actualización documental.

Para repetir validación técnica, desde la raíz, solo en entornos desechables:

```bash
python3 -m scripts.ci
python3 -m scripts.restore_drill
python3 -m scripts.validate_tls_proxy
python3 -m scripts.browser_qa
```

## Pendientes que impiden declarar cierre total

Restauración demostrada de la copia del despliegue anterior, release/secretos y
operación programada en servidor, despliegue con certificado/cadena/renovación
reales y aceptación de incorporación/QR/códigos/recuperación por correo y teléfono.
Son pendientes explícitos, no se sustituyen por SMTP simulado, TLS temporal ni un
CI verde. Se mantienen aplazados según `staging-plan.md`; no se ejecutó SSH ni se
modificaron datos remotos durante esta auditoría. No se declara completo el objetivo
operativo de seguridad ni se amplía el alcance con nuevos mecanismos opcionales.

## Repositorio y herramientas aplazadas

Cinco herramientas/tests experimentales de VPS sin seguimiento se preservaron en
`.local/deferred-vps/legacy-tools-9e58e2f/`, con estructura original y manifiesto
SHA-256 verificado. No había referencias desde archivos seguidos (`git grep`), no
se modificó su contenido ni se incluyeron en producción. Pueden recuperarse a sus
rutas originales al retomar esa tarea, verificando el manifiesto y sin sobrescribir
archivos nuevos. No se borraron copias de datos del usuario.

Se ejecutó `python3 -m unittest discover -s scripts/tests`: **46 pruebas seguidas
aprobadas**, log `.local/validation/access-current-script-tests.log`. Los 52 casos
de los CI anteriores incluían seis tests de esos experimentos; se conserva ese
hecho en las entregas históricas. `git diff --check` aprobado. Los únicos cambios
seguidos de esta auditoría son documentación; no requiere reiniciar la aplicación.

Próximo trabajo local autorizado: imágenes privadas de productos en Contabo.
Endpoint/bucket solicitados sin claves; la configuración real sigue sin verificarse.
