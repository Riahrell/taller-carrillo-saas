# Corrección del bloqueo certifi

ops/security/requirements.lock: certifi 2026.7.22 → 2026.10.19 y sus dos hashes
SHA-256 oficiales. Única dependencia modificada. Es tooling de pip-audit;
los locks de aplicación/desarrollo no contienen certifi y permanecen intactos.
No se añadió una excepción ni se debilitó el gate.

Regeneración ejecutada desde la raíz:
```bash
.venv/bin/pip-compile --upgrade-package certifi==2026.10.19 --generate-hashes --no-emit-index-url --no-emit-trusted-host --strip-extras --cache-dir /tmp/carrillo-security-pip-cache --output-file=ops/security/requirements.lock ops/security/requirements.in
docker build -t carrillo-security-certifi-fix ops/security
docker run --rm --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges -v "$PWD/backend/requirements.lock:/source/backend.lock:ro" -v "$PWD/backend/requirements-dev.lock:/source/backend-dev.lock:ro" -v "$PWD/ops/security/requirements.lock:/source/security.lock:ro" carrillo-security-certifi-fix --disable-pip --no-deps --require-hashes --strict --progress-spinner=off --desc=off --format=json --timeout=15 -r /source/backend.lock -r /source/backend-dev.lock -r /source/security.lock
git diff --check
```

Resultados reales: regeneración correcta; imagen construida con instalación
--require-hashes; auditoría de los tres locks con 78 dependencias y cero
vulnerabilidades conocidas, salida 0. Evidencia local en
.local/validation/certifi-{lock.log,security-build.log,python-audit.json,python-audit.log}.
No se ejecutaron pruebas funcionales ni CI completa local. La ejecución remota
de GitHub deberá confirmar los demás controles; no se afirma que ya pasó.

Fuente de versión y hashes: https://pypi.org/project/certifi/2026.10.19/
Archivos cambiados: lock citado y este registro.
Siguiente tarea: revisar el resultado remoto y continuar con la interfaz de compras.
