# Entrega: primitivas MFA (acceso 2.21)

Fecha: 2026-10-17 (America/El_Salvador).
Rama: `codex/mfa-recovery-contract`.
**Biblioteca interna; MFA y recuperación todavía no están activos en el acceso.**

## Resultado

`backend/app/modules/access/mfa_crypto.py` proporciona:

- TOTP mediante PyOTP 2.10.0: semilla CSPRNG de 160 bits, SHA-1 explícito,
  seis dígitos ASCII, paso de 30 segundos y ventana ±1. Devuelve un contador
  candidato solo si coincide de forma única y supera al consumido. Rechaza
  regresión respecto a la hora de verificación anterior, incluso subsegundo.
- AES-256-GCM mediante cryptography 50.0.2: nonce aleatorio de 96 bits,
  sobre v1 y AAD con propósito/versión, usuario, credencial y key id. Keyring
  externo inyectado; escribe con clave activa y lee con anteriores disponibles.
  Desprotección con identidad, tag, nonce, versión o clave incorrectos falla
  cerrada con un error genérico, sin restablecer el factor.
- Diez códigos offline independientes de 128 bits, Base32 canónico sin padding.
  SHA-256 separado por dominio/usuario/conjunto, comparación constante y rechazo
  de variantes ambiguas. El hash no depende de contraseña; su restablecimiento
  futuro no invalidará por accidente estos códigos sin usar.
- Semillas y códigos envueltos en SecretBytes/SecretStr, modelos congelados y
  campos excluidos de repr/dumps. No hay garantías de borrado seguro de memoria.

No hay rutas, QR, tablas, migración, variables de entorno nuevas ni claves o
códigos operativos. El esquema sigue en **0008_session_reauthentication**.
La biblioteca no se llama desde login, reautenticación o emisión de sesión.
No se modifican cuentas, contraseñas ni datos del entorno de desarrollo.

La igualdad de un código **no constituye autorización ni lo consume**. La
siguiente entrega debe usar locks/CAS y persistir contador/hora/consumo dentro de
la misma transacción. Dos procesos aún podrían aceptar el mismo candidato si
se usara esta biblioteca sin ese servicio; por eso no se ha conectado al acceso.
La hora y estado deben provenir del servidor, nunca de la petición del cliente.
El primer uso no puede detectar por sí solo un reloj ya atrasado: requiere
supervisión de tiempo en operación. La ventana no significa vigencia máxima
exacta de treinta segundos.

## Archivos completos creados o modificados

- `backend/app/modules/access/mfa_crypto.py`: primitivas internas y límites.
- `backend/tests/test_mfa_crypto.py`: fixtures sintéticos/públicos, vectores RFC,
  tolerancia, reloj, repetición/ambigüedad, AEAD/AAD/rotación, códigos y secretos.
- `backend/pyproject.toml`, `backend/requirements.lock`,
  `backend/requirements-dev.lock`: dos dependencias nuevas fijadas con hashes;
  sin actualizar las demás versiones. Dev conserva restricciones del runtime y
  hashes también de pip/setuptools para instalación aislada.
- `README.md`, `docs/acceptance.md`, `docs/data-model.md`, `docs/decisions.md`,
  `docs/mfa-recovery-contract.md`, `docs/security-review.md`,
  `docs/reviews/plan-maestro-v1.1.md` y este registro: estado, evidencia, límites
  y siguiente tarea. Entregas anteriores mantienen su evidencia histórica.

Archivos completos disponibles en el repositorio. Sin cambios frontend o de
contratos HTTP; no se requiere un recorrido nuevo de navegador para esta biblioteca.

## Comandos y validación

Desde la raíz del repositorio, con el entorno existente Python 3.13:

```bash
.venv/bin/pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host --output-file=backend/requirements.lock --strip-extras backend/pyproject.toml
.venv/bin/pip-compile --allow-unsafe --constraint=backend/requirements.lock --extra=dev --generate-hashes --no-emit-index-url --no-emit-trusted-host --output-file=backend/requirements-dev.lock --strip-extras backend/pyproject.toml
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.lock
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
```

Prueba focal, desde `backend`:

```bash
../.venv/bin/python -m pytest tests/test_mfa_crypto.py -q
```

Validación integral, desde la raíz; requiere Docker y red para auditoría/builds,
usa credenciales efímeras ocultas y PostgreSQL aislado:

```bash
python3 -m scripts.ci > /tmp/carrillo-access-2.21-ci-final.log 2>&1
```

Resultados reales de esta entrega:

- Pruebas focales: **77 passed**, exit 0.
- Ruff, formato y whitespace: exit 0; 76 archivos Python formateados.
- CI final: **exit 0**, proyecto aislado `carrillo-ci-b5e6625ee363`.
  **1.006 pruebas:** 811 backend (incluyen las 77 nuevas y PostgreSQL),
  7 por proxy, 152 frontend y 36 de scripts. Sin skips atribuidos a MFA.
  Workflow, tipos, compilaciones, migraciones, HTTP smoke y limpieza CI pasaron.
  Primera ejecución también pasó; la final incorpora tres casos adicionales
  y el parámetro SHA-1 explícito. No se suman ambas ejecuciones como pruebas únicas.
- Seguridad: canary, secretos del worktree/historial y locks Python passed.
  npm accepted con las mismas cuatro entradas SEC-2026-001, no cero avisos.
  Informe privado: `.local/security-reports/7191a06957024d18888b5ca781b03a81.json`.
- Versiones previas de ambos locks comparadas contra HEAD: todas conservadas;
  únicamente se agregaron cryptography y pyotp. 22 enlaces relativos resueltos.
- Actualización local: build backend, recreación y recarga del proxy exit 0.
  HTTP smoke exit 0 (HTML español, 10 assets, API/origen, cabeceras y rechazo
  de escritura sin CSRF/origen). Backend, DB, frontend y proxy **healthy**.
  Aplicación en `http://localhost:8080`, sin cambios de credenciales/login.
- No hubo prueba nueva de navegador ni autenticación manual de cuenta real.

Comandos de actualización local ejecutados después de validar, sin regenerar secretos,
bootstrap o migración:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.21-dev-build.log 2>&1
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait --no-deps backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

## Fuentes y decisiones

[RFC 6238](https://www.rfc-editor.org/rfc/rfc6238), apéndice B, proporciona los
vectores públicos; se verifica también el perfil de seis dígitos del proyecto.
[PyOTP](https://pyauth.github.io/pyotp/) aporta el cálculo del contador OTP;
el consumo/replay y los límites son responsabilidad de la integración.
[AESGCM de cryptography](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM)
implementa cifrado autenticado con AAD. No se implementan AES o HMAC manualmente.
Versiones consultadas en PyPI el 2026-10-17 y fijadas en locks; consultar fuentes
no sustituye auditoría o pruebas del proyecto.

## Riesgos y siguiente tarea

MFA activo, persistencia atómica, cuotas compartidas, verificación de buzón,
recuperación, sesiones y HTTP/UI siguen pendientes. Las excepciones de validación
no deben registrarse con inputs: usar `errors(include_input=False)`; los modelos
ocultan inputs en el texto ordinario del error, no en toda introspección posible.
No utilizar `model_construct`/`model_copy(update=...)` para introducir secretos
sin validar. TOTP sigue expuesto a phishing; passkeys son una mejora posterior.

Custodia/rotación/backups de claves, supervisión de reloj y restauración sin
revivir factores/códigos consumidos necesitan ensayos antes de producción.
SEC-2026-001 conserva el aviso dev-only npm (cuatro entradas del mismo aviso),
con vencimiento 2026-10-31 00:00 UTC; no se amplía ni se declara corregido.
Sin certificación ASVS/AAL2 o preparación productiva por pasar pruebas.

**Siguiente 2.22:** persistencia de credenciales y consumo MFA con servicios
transaccionales, concurrencia y rollback en PostgreSQL aislado. La activación
completa del login vendrá después de cubrir todos sus caminos y recuperación.

Sin envíos de correo, compras, acceso/limpieza de VPS, DNS/TLS, despliegue público
o push remoto en esta entrega.
