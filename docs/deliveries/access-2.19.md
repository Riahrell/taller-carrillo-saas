# Entrega: escaneos de seguridad (acceso 2.19)

Fecha: 2026-10-17 (America/El_Salvador).
Rama: `codex/security-scans`.

## Resultado

`python3 -m scripts.security` ejecuta revisiones aisladas de secretos en archivos
publicables/historial completo y de dependencias Python/npm, incluido desarrollo
y tooling de auditoría. `scripts.ci` las exige antes de construir/arrancar pruebas
de la aplicación. Un fallo de red, un paquete omitido o un informe inválido bloquea
la validación; no se trata como resultado limpio.

Gitleaks 8.30.1 y Node/npm usan imágenes fijadas por digest. pip-audit 2.10.1
tiene lock propio con hashes, separado de runtime. No se instalan ni ejecutan
paquetes de la aplicación para auditarlos y no se aplica audit fix. Contenedores
con privilegios reducidos, raíz read-only y montajes mínimos. El detector de
secretos no tiene red; auditores con red solo reciben manifiestos/locks, nunca
código, credenciales, historial o reportes de secretos. Sus consultas se bloquean
si el detector falla/encuentra secretos.

Una prueba real en cada ejecución verifica detección/redacción de un token
sintético en archivo y en historial después de borrarlo. Informe persistente
saneado y privado, con hashes de entradas, fecha, commit base y estados. El
snapshot incluye cambios actuales y archivos nuevos no ignorados; el commit base
no implica que fuera un árbol limpio. No exporta .local o secretos operativos.

**Hay un aviso abierto**, GHSA-vfj7-8cjw-p6xm/CVE-2026-93687, con cuatro entradas
npm de severidad alta en la cadena dev-only de ESLint/Next. Sin parche publicado.
SEC-2026-001 permite temporalmente validar desarrollo hasta **2026-10-31 00:00 UTC**,
con versiones, ubicación, advisory y dev=true exactos. Permanece visible como
accepted, no se presenta corregido. Cualquier otro aviso o ámbito no cubierto
bloquea; vencimiento/cambio de versión o paso a runtime requiere revisión.
No retirar reglas o hacer downgrade mayor para ocultarlo.

Evidencia, razonamiento, referencia oficial y límites en
`../security-scanning.md`. Esta aceptación técnica no autoriza producción.

## Archivos completos creados o modificados

- `scripts/security.py`: snapshot/mirror privados, contenedores, canary, parsers
  que fallan ante resultados incompletos, política temporal y reportes saneados.
- `scripts/tests/test_security.py`: **23 pruebas nuevas** de parsing/redacción,
  estados, alcance de archivos, entorno, permisos, puerta CI y excepciones.
- `ops/security/Dockerfile`, `requirements.in` y `requirements.lock`: herramienta
  Python separada, versión/dependencias fijadas con hashes.
- `ops/security/gitleaks.toml`: reglas por defecto, sin silencios generales.
- `ops/security/exceptions.json`: única excepción, alcance exacto y vencimiento.
- `scripts/ci.py`: escaneos obligatorios antes de construir/probar aplicación.
- `.github/workflows/ci.yml`: historial completo; conserva checkout fijado y
  credenciales no persistidas.
- `docs/security-scanning.md`: operación, alcance, estados, política y hallazgo.
- `README.md`, `docs/acceptance.md`, `docs/security-review.md`,
  `docs/decisions.md`, `docs/reviews/plan-maestro-v1.1.md`,
  `docs/email-recovery-design.md`, `docs/data-model.md` y este registro: estado y
  siguiente tarea; se corrigió la referencia antigua a guardia aún desconectada.

Archivos completos en el repositorio. Sin cambios de aplicación/backend/frontend,
esquema, lockfiles de aplicación, credenciales operativas, DNS o VPS. La DB sigue
en 0008. Las herramientas de seguridad no se agregan a las imágenes de runtime.

## Comandos exactos ejecutados

Desde la raíz:

```bash
docker pull ghcr.io/gitleaks/gitleaks:v8.30.1
docker run --rm --network none ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f version
.venv/bin/pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host --strip-extras --cache-dir /tmp/carrillo-security-pip-cache --output-file=ops/security/requirements.lock ops/security/requirements.in > /tmp/carrillo-access-2.19-lock.log 2>&1
python3 -m unittest discover -s scripts/tests > /tmp/carrillo-access-2.19-unit.log 2>&1
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.security > /tmp/carrillo-access-2.19-security.log 2>&1
python3 -m scripts.security > /tmp/carrillo-access-2.19-security-verified.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.19-ci.log 2>&1
python3 -m scripts.ci > /tmp/carrillo-access-2.19-ci-verified.log 2>&1
```

El runner construye el auditor Python con el lock separado; no instalarlo en
la venv operativa ni modificar los guards de integración. CI genera PostgreSQL,
credenciales y proyecto propios, que limpia al finalizar. Requiere Docker/Git
e internet para avisos y descargas no cacheadas; no ofrece un bypass offline.

Verificación del desarrollo existente, sin reinicio o recreación:

```bash
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

## Resultados reales

- Imagen de Gitleaks descargada y digest resuelto; ejecución sin red devolvió
  **v8.30.1**. Lock de pip-audit generado con Python 3.13/pip-compile 7.6.2.
- Primer escaneo standalone: **exit 1**, cero secretos detectados en snapshot e
  historial, Python sin avisos conocidos y **cuatro entradas npm de un solo aviso**.
  Se consultaron aviso GitHub y registro oficial: braces más reciente 3.0.3,
  sin versión corregida. No se ejecutó el downgrade 16 → 14 propuesto por npm.
- Escaneo standalone con excepción exacta: **exit 0**, cuatro etapas passed y
  npm-lock accepted con las cuatro entradas visibles. Informe privado:
  `.local/security-reports/1f1b5beb321a4d07968a5f759159b904.json`.
- Primer CI, **carrillo-ci-be492d6b864a**, falló en Ruff con ocho líneas demasiado
  largas. La comprobación local previa no había usado el config explícito para
  scripts; se ajustaron líneas y se repitió con el mismo config de CI. No se
  desactivó la regla ni el gate.
- CI final, **carrillo-ci-f8ca1b0acf25**, **exit 0 / CI passed**:
  **734 backend en 60.08s** (228 unitarios + 506 PostgreSQL), **7 por proxy en
  6.31s**, **152 frontend en 1.51s** y **36 scripts/configuración**. Total
  **929 pruebas**, 23 nuevas respecto a 2.18. No sumar repeticiones locales.
- Escaneos dentro de CI: detector-self-test, secrets-worktree, secrets-history
  y python-locks passed; npm-lock accepted con SEC-2026-001. Informe privado:
  `.local/security-reports/141f25f65b2a4ea293f27ba3840aee63.json`.
  Los canaries reales del detector se ejecutaron además de las 929 pruebas;
  no se agregan como pruebas de aplicación ni cuentan el secreto ficticio como
  un secreto encontrado en el proyecto.
- Pruebas nuevas: secretos omitidos del reporte; error/JSON/exit inconsistentes;
  dependencia saltada; alcance shallow rechazado; archivos ignorados excluidos;
  cambios/new incluidos; symlink privado rechazado; entorno operacional fuera;
  restricciones del contenedor; puerta CI antes de build/DB; excepción visible,
  vencimiento exacto, plazo/futuro, versión, runtime, otro aviso, anidación y
  severidad crítica. Simulaciones no se presentan como cortes reales de internet.
- Ruff/formato de **74 archivos Python**, pip check, actionlint, ESLint, Prettier,
  tipos, migraciones, tests, builds y smoke aprobados. Frontend/backend de
  aplicación reutilizan caché; no se afirma una compilación nueva de Next.js.
- Proyecto CI eliminado. No hubo recorrido nuevo de navegador: no cambia UI.
  El entorno local conservó cuatro servicios **healthy**; smoke exit 0 con HTML
  español, diez assets, API mismo origen, 404 y rechazo sin origen/CSRF válidos.
  No se tocó una cuenta operativa ni se ensayó restauración de backup.

## Riesgos pendientes y siguiente tarea

SEC-2026-001 continúa abierto y vence el 2026-10-31 UTC. CI mostrará el aviso en
cada ejecución y bloqueará al vencer; no hay recordatorio o monitor programado.
Revisar corrección compatible o sustitución con controles equivalentes, sin
ampliación automática. No son cuatro vulnerabilidades independientes ni cero
vulnerabilidades por tener exit 0 con una excepción.

La cobertura está limitada a patrones de secretos y avisos conocidos de los
ecosistemas auditados. Falta análisis de imágenes/SO, SAST, SBOM, revisión ASVS,
restauración aislada, auditoría ampliada, CSP y controles operativos/HTTPS.
El resultado puede cambiar con la base de avisos sin cambiar el código.
CI remoto GitHub sigue pendiente; aquí se ejecutó el mismo runner localmente.

**Siguiente 2.20: contrato de MFA obligatorio para administradores y recuperación**,
coordinando buzón verificado, recuperación por correo y revocación global. No
emitir sesión antes de completar el factor ni convertir el correo en un bypass.
La recuperación por correo sigue como diseño, sin endpoint o envíos activos.

No se necesita comprar servicios para esta entrega o el contrato siguiente.
No hubo SSH, limpieza de VPS, DNS/TLS, despliegue público o push remoto.
