# Acceso 2.39: integración de identidad e imágenes verificadas

## Resultado y alcance

MFA, verificación de buzón, consumo de reset, recuperación offline, cambio de
factor, códigos y reautenticación están integrados en API/interfaz. La base de
implementación se guardó en `323ba97`; esta entrega añade presentación adaptable,
validación del campo TOTP, imágenes de ejecución mínimas, escaneo/SBOM en CI y
provisión de secretos compatible con permisos restrictivos.

No equivale a cierre operativo: no se ha desplegado esta versión en Contabo.
No hay inventario, ventas, métodos de pago o reglas comerciales nuevas.

## Cambios

- Reset ciego en cola cifrada, consumo único, revocación global y avisos/auditoría
  atómicos, conservando MFA. Verificación general de buzón ligada a sesión/dirección.
- Contraseña más TOTP antes de sesión/selección; reautenticación sin extender la
  vida absoluta. Cambios de rol revocan las sesiones del usuario en todas sus
  empresas. El último administrador necesita un factor confirmado.
- Worker de la misma imagen backend, SMTP TLS con deadline y clasificación de
  fallos, mantenimiento, claves externas, recifrado y cuarentena de restauración.
- Formularios de identidad con campos apilados y controles adaptables. TOTP exige
  seis dígitos, sin limitar a seis los códigos offline. El cierre global descarta
  configuración pendiente en la interfaz. No se persisten secretos en el navegador.
- Backend/frontend Alpine fijados por digest, sin pip/npm en ejecución. PostgreSQL
  arranca como UID 70, sin capacidades y con filesystem de solo lectura salvo
  volúmenes/tmpfs; retira el helper gosu innecesario. Proxy con libexpat parcheado.
  zlib y nghttp2 se fijan en versiones corregidas. Sin cambios de versión mayor
  de PostgreSQL del proyecto ni de arquitectura de aplicación.
- Trivy fijado por digest, análisis de archivos de imagen sin red/socket Docker,
  reportes de todas las severidades y CycloneDX. HIGH/CRITICAL bloquean; un análisis
  incompleto falla. Workflow conserva JSON/SBOM siete días mediante acción fijada.
- Secretos recién creados se escriben primero privados, se sincronizan y pasan a
  0444 dentro de directorios 0700 del operador para los mounts de usuarios distintos.
  Se rechazan padres públicos, enlaces como directorio y sobrescrituras. Se corrigió
  un arranque fallido al usar umask 077; no se relajó la privacidad del directorio.
- Producción exige las cuatro referencias de imágenes explícitas. Desarrollo y
  pruebas conservan configuración y almacenamiento separados.

Archivos completos en el repositorio: módulos de identidad y herramientas en
`backend/app/modules/access/` y `backend/app/tools/`; migraciones 0025–0029;
componentes/páginas de identidad y `frontend/src/app/globals.css`; Dockerfiles
backend/frontend/PostgreSQL/proxy; Compose; scripts y pruebas de operación.
El manifiesto exacto del cambio se obtiene mediante `git show --stat 323ba97` y
`git show --stat HEAD` tras guardar esta entrega. No hay claves reales versionadas.

## Validación ejecutada

Comandos desde la raíz, con acceso autorizado a Docker:

```bash
umask 077
python3 -m scripts.ci
python3 -m scripts.restore_drill
python3 -m scripts.validate_tls_proxy
python3 -m unittest discover -s scripts/tests
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Resultados reales del 18 de octubre de 2026:

- CI completo: **1300 backend, 156 frontend y 7 de proxy** aprobados, además de
  **46 pruebas de scripts**, lint, formato, tipos, builds, workflow y smoke HTTP.
  Log privado persistente: `.local/validation/hardened-ci-umask.log`.
- Las cuatro imágenes construidas: **cero hallazgos del escáner** y SBOM con
  componentes, IDs inmutables y fecha/hash de la base. Evidencia privada en
  `.local/image-reports/190edf7cfcb548a982f9e1e4842df587/`.
  No implica ausencia de vulnerabilidades desconocidas ni vale para otra imagen.
- Restauración PostgreSQL completa, descifrado del factor con clave externa y
  cuarentena que conserva MFA: pasa. Log `.local/validation/hardened-restore.log`.
  Datos sintéticos y DB aislada, sin datos de Contabo.
- Nginx parcheado: configuración TLS y lectura como UID 101 pasan con certificado
  temporal. Log `.local/validation/hardened-tls.log`. No prueba HTTPS público.
- Revisión manual anterior en navegador efímero: login/TOTP/selección/seguridad,
  cierre global y presentación a 390 px sin desbordamiento ni errores de consola;
  QR cargado desde data URL local. No se confirmó un cambio de credencial real.

Las ejecuciones interrumpidas/perdidas y el arranque fallido bajo umask 077 no
cuentan como aprobados. La evidencia citada es la repetición completa posterior.
Los cuatro avisos npm dev-only conservan SEC-2026-001, con vencimiento original
2026-10-31. No aplicar esa excepción a las imágenes de ejecución ni ocultarla.

## Riesgos pendientes y siguiente tarea

1. Crear y verificar respaldo recuperable del despliegue anterior de Contabo.
   El usuario autorizó retirarlo; todavía sigue activo. No reutilizar su volumen
   PostgreSQL 16 como almacenamiento de PostgreSQL 18 del sistema nuevo.
2. Preparar release y secretos externos; desplegar en el panel con certificado
   válido, renovación probada y únicamente puertos web públicos necesarios.
3. Probar mensajes operativos, incorporación/QR/códigos y recuperación reales,
   cookies HTTPS y acceso desde teléfono. Dos correos de transporte anteriores
   sí fueron recibidos por el único destinatario autorizado; no prueban estos flujos.
4. Revisar criterio por criterio `docs/access-security-closure.md` antes de abrir
   inventario. La recuperación excepcional permanece denegada sin evidencia y
   separación operativa suficientes, sin bypass por correo o administrador.

No se aplicaron migraciones a desarrollo ni al VPS ni se borraron archivos remotos.
