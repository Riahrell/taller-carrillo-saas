# Entrega: contrato MFA y recuperación (acceso 2.20)

Fecha: 2026-10-17 (America/El_Salvador).
Rama: `codex/mfa-recovery-contract`.
**Entrega documental. Runtime sigue en 2.19, sin MFA o recuperación activos.**

## Resultado

Contrato previo a implementar, en `../mfa-recovery-contract.md`: factor TOTP del
usuario global, obligatoriedad admin, estados pendientes sin sesión operativa,
incorporación con prueba fresca de buzón y recuperación offline separada del
restablecimiento de contraseña por correo. No se necesita resolver proveedor
de correo o comprar servicios para comenzar la biblioteca interna siguiente.

Se revisaron los servicios actuales. `_authenticate` emite sesión tras contraseña
si existe una membresía o una empresa ya seleccionada; la selección multiempresa
tiene pendiente propio. `issue_session` no comprueba segundo factor y
`resolve_session` no tiene evidencia/revisión MFA. La reautenticación administrativa
solo verifica contraseña. No bastará agregar una pantalla OTP: se deberán cubrir
todos esos caminos al integrar MFA, sin cambiar ahora sus contratos HTTP.

La promoción a admin actualmente revoca sesiones solo de esa membresía y pendientes
globales. El diseño futuro exige invalidar pruebas/sesiones globales cuando imponga
MFA, sin permitir al admin concedente modificar el factor/correo de otra persona.
No se declara esa revocación global implementada por escribirla en el contrato.

La futura protección de último admin deberá comprobar incorporación MFA completa:
un segundo admin con factor pendiente no permite retirar al último con factor
confirmado. Queda como aceptación pendiente junto con las carreras de incorporación.

Decisiones concretas: TOTP con contador consumido entre procesos/propósitos;
cuota separada que no se limpia al acertar contraseña; semilla cifrada con AEAD
y keyring externo; códigos offline de un solo uso; revisión de seguridad y cambios
atómicos; prueba combinada reciente medida desde la verificación más antigua.
Un login MFA completo podrá reutilizar prueba reciente para evitar solicitar
inmediatamente otro código, con validación de toda escritura en el servidor.

Se corrigió una ambigüedad del diseño de correo: restablecer contraseña invalida
otros enlaces de contraseña/sesiones/pendientes, pero conserva factor y códigos
offline MFA sin usar. El correo no permite desactivar o regenerar MFA. Perder
teléfono y códigos necesita prueba independiente/runbook excepcional antes de
producción; no se crea un bypass para soporte o el último administrador.

TOTP no resiste phishing. Se preserva passkeys/WebAuthn como ampliación prioritaria;
sin certificación AAL2/ASVS inferida. Criterios de aceptación de criptografía,
concurrencia, rollback, límites, rutas, UI, correo y operación quedan explícitos
como **pendientes**, no como pruebas ejecutadas.

## Archivos completos creados o modificados

- `docs/mfa-recovery-contract.md`: contrato, estados, parámetros, modelo lógico,
  incorporación/sustitución/recuperación, integración y aceptación futura.
- `docs/email-recovery-design.md`: correo preserva factor y códigos offline;
  enlaza al contrato conjunto y distingue diseño de runtime.
- `docs/auth-http-contract.md`, `docs/reauthentication-contract.md`: rutas vigentes
  siguen password-only; cambios MFA/UX futuros separados del estado actual.
- `docs/permissions.md`, `docs/data-model.md`: límites global/tenant, revocaciones
  futuras y modelo lógico propuesto, sin tablas o permisos nuevos activos.
- `README.md`, `docs/acceptance.md`, `docs/security-review.md`,
  `docs/security-scanning.md`, `docs/reviews/plan-maestro-v1.1.md`,
  `docs/decisions.md` y este registro: estado, trazabilidad y próxima tarea 2.21.

Todos los archivos se entregan completos en el repositorio. Sin modificaciones
de código, dependencias, imágenes de aplicación, DB, cuentas o contraseñas reales.
Esquema permanece en 0008; no se generan semillas, claves, QR o códigos operativos.

## Validación de esta entrega

Revisión de documentos contra código local y fuentes oficiales: RFC 6238,
OWASP MFA y NIST SP 800-63B-4, enlazadas en el contrato. Los parámetros y decisiones
de arquitectura del proyecto no se presentan como plazos impuestos por esas fuentes.

Comandos de validación, desde la raíz:

```bash
git diff --check
python3 -m scripts.security > /tmp/carrillo-access-2.20-security.log 2>&1
```

Resultados reales:

- `git diff --check`: exit 0, sin errores de whitespace.
- Enlaces relativos comprobados con lectura local: **26**, todos resueltos.
- `scripts.security`: **exit 0**. Canary del detector, secretos en worktree,
  secretos en historial y Python passed; npm accepted con las cuatro entradas
  del mismo aviso dev-only SEC-2026-001. No significa cero vulnerabilidades.
  Informe privado: `.local/security-reports/0ffa5f3b9d7c4bfda7e75b005b773dfa.json`.

No se ejecuta la suite funcional ni se recompila la aplicación para un cambio
solo documental. Las 929 pruebas de 2.19 son evidencia histórica, no una ejecución
nueva ni demostración de MFA funcionando. No hubo recorrido nuevo de navegador.

## Riesgos y siguiente tarea

MFA, verificación de buzón y recuperación **siguen sin implementar**. TOTP tiene
riesgo de phishing, y perder todos los medios de recuperación exige un proceso
independiente por definir/probar. Custodia/rotación/respaldo de claves, restauración
sin revivir códigos, incorporación del primer admin, correo y HTTPS se deben
ensayar antes de activar cuentas reales o exponer la aplicación públicamente.

SEC-2026-001 de tooling de desarrollo continúa visible hasta su revisión y vence
el 2026-10-31 00:00 UTC. Este contrato no modifica o amplía la excepción.
No hay monitoreo programado, preparación productiva o certificación declarados.

**Siguiente 2.21:** biblioteca interna TOTP/protección de semillas/códigos offline,
con dependencias mantenidas, fijadas y auditadas; vectores RFC, límites de tiempo,
tratamiento de secretos, cifrado/AAD y errores. Sin rutas, migración o cambio de
login en esa tarea. Persistencia/transacciones e integración completa vendrán
después, en entregas pequeñas.

No hubo envíos de correo, SMS, compras, acceso/limpieza de VPS, DNS/TLS,
despliegue público o push remoto. La aplicación local conserva su versión vigente.
