# Inventario 3.3 — Altas internas autorizadas y auditadas

`CatalogAdministrator` crea productos/categorías con empresa y actor de sesión,
administrador y confirmación reciente. Datos extra rechazados, precio Decimal
exacto y categoría activa de la misma empresa. Alta y evento tipado se confirman
juntos; verificación final dentro de la transacción. No se devuelve resultado si
falla commit, ni hay replay automático. Contrato en `../catalog-contract.md`.

`0031_catalog_audit` amplía la auditoría append-only existente, conservando formas
de eventos de membresía. FK compuestas para objetivos, snapshots iniciales y
downgrade que conserva evidencia. La guardia de 0030 ahora bloquea ambas tablas
antes de verificar que estén vacías, para cerrar la carrera entre comprobación y
borrado. 0030 nunca se había aplicado en desarrollo/VPS; solo bases efímeras.

## Archivos

- `backend/app/modules/catalog/inputs.py`, `service.py`.
- `backend/app/modules/access/models.py`: auditoría tipada y registro de modelos
  referenciados, también al cargar acceso sin fixtures de catálogo.
- `backend/migrations/versions/0031_catalog_audit.py`, guardia de `0030_catalog.py`.
- `backend/tests/test_catalog_inputs.py`, `test_model_registration.py` y
  `integration/test_catalog_service.py`.
- Revisiones esperadas actualizadas en `integration/test_catalog.py`,
  `test_mfa_consumption.py`, `test_postgres.py`, `test_user_provisioning.py`.
- Contrato, modelo, permisos, README y esta entrega.

## Validación

Comandos desde la raíz, Docker con permiso y PostgreSQL efímero del runner:

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_catalog_inputs.py -q
python3 -m scripts.validate_identity --test-path tests/integration/test_catalog_service.py --test-path tests/integration/test_catalog.py --test-path tests/integration/test_postgres.py
python3 -m scripts.validate_identity --full-backend
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_model_registration.py backend/tests/test_catalog_inputs.py -q
python3 -m scripts.validate_identity --test-path tests/integration/test_catalog_service.py --test-path tests/integration/test_postgres.py --test-path tests/test_catalog_inputs.py --test-path tests/test_model_registration.py
python3 -m scripts.validate_identity --test-path tests/integration/test_catalog_service.py --test-path tests/integration/test_catalog.py --test-path tests/integration/test_postgres.py --test-path tests/test_catalog_inputs.py --test-path tests/test_model_registration.py
.venv/bin/ruff check --config backend/pyproject.toml backend scripts
.venv/bin/ruff format --check --config backend/pyproject.toml backend scripts
git diff --check
```

Primeras comprobaciones: 43 unitarias y 37 de servicios/esquema pasan. Regresión
completa: **1414 pasan**. Esta ejecución precedió al ajuste de registro de modelos,
la guardia concurrente de downgrade y tres regresiones adicionales. Después se
ejecutaron las pruebas pertinentes sobre los archivos finales; no atribuir al
primer run la cobertura de esas correcciones. Comprobación intermedia: 44 locales
y 75 en el runner pasan. Logs privados en `.local/validation/catalog-3.3-*.log`.

Verificación final: **92 pruebas pasan**, runner código 0, migraciones y Alembic
check sin diferencias, recursos efímeros retirados. Ruff check, formato de 178
archivos Python y `git diff --check` pasan. Evidencia final:
`.local/validation/catalog-3.3-verified.log`. Los recuentos de ejecuciones se
solapan; no sumarlos como pruebas distintas ni como CI integral de toda la web.

Las pruebas incluyen permisos actuales, revocación/contexto/confirmación,
categoría inexistente/ajena/inactiva, colisión de SKU con dos llamadas concurrentes,
fallo SQL real al escribir auditoría, fallo simulado antes de commit, autorización
final fallida, evidencia incompleta/ajena, UPDATE/DELETE denegados y downgrade
que conserva el evento. La prueba de carga usa un proceso nuevo para evitar que
imports de fixtures oculten referencias SQLAlchemy sin registrar. La prueba de
colisión no demuestra espera bloqueada específica ni sustituye futuras pruebas
concurrentes de stock.

## Límites y siguiente tarea

Sin HTTP/CSRF/UI de catálogo ni cambios de stock. Las altas todavía no tienen
idempotencia general: respuesta perdida no permite repetir automáticamente.
Sin edición/desactivación ni consulta/paginación autorizada de catálogo todavía.
No se aplicaron migraciones en desarrollo ni VPS ni se ejecutó frontend/browser.
Las herramientas de respaldo anteriores siguen sin seguimiento de Git y fuera
de esta entrega; no se declara el repositorio completo limpio ni un hito cerrado.

Siguiente tarea: consultas acotadas por empresa y transporte administrativo con
CSRF/origen, validación y respuesta ligada al contexto, antes de conectar los
formularios. VPS/HTTPS y restauración remota siguen pospuestos.
