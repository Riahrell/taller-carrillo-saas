# 3.56 — Corrección del bloqueo de estilo en GitHub

El CI aplicaba backend/pyproject.toml al directorio scripts, mientras la comprobación
local sin --config usaba las reglas por defecto. Eso omitía E501. Se dividen las
dos cadenas largas de restore_drill.py y otra de retire_legacy_vps.py en literales
adyacentes sin cambiar su contenido ni comportamiento. ruff.toml en la raíz hereda
la configuración del backend para que las comprobaciones locales de scripts
usen las mismas reglas que CI.

Archivos completos cambiados: scripts/restore_drill.py,
scripts/retire_legacy_vps.py, ruff.toml y este documento.

Comandos ejecutados:

```sh
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/ruff check scripts
python3 -m unittest scripts.tests.test_restore_drill scripts.tests.test_retire_legacy_vps
```

Resultados reales: lint correcto, 276 archivos con formato correcto y cuatro
pruebas aprobadas. Además se creó un proyecto temporal mediante scripts.ci.Pipeline,
se validó su aislamiento, se construyó tests y se ejecutó el servicio backend-lint
con run --rm --no-deps. Ese es el mismo contenedor/comando de CI: Ruff y formato
aprobados y pip check sin dependencias rotas. Log ignorado:
.local/validation/ci-style-3.56.log. No se repite la suite completa de la app ni el
ensayo S3: solo cambian cadenas de diagnóstico y configuración de lint.

Esto resuelve el error reproducido localmente; el resultado de una nueva ejecución
remota completa de GitHub no se ha confirmado. No se modifican excepciones de
seguridad, dependencias o reglas comerciales.
