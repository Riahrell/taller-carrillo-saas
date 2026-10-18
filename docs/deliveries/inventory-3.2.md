# Inventario 3.2 — Persistencia del catálogo

Implementadas categorías y productos con empresa, SKU único por empresa y
referencia compuesta a categoría. Estado desactivable y precio Decimal obligatorio
sin valor por defecto; no hay campos de stock ni publicación. Runtime sin DELETE;
el downgrade se rechaza ante datos, para no borrar catálogo operativo.
Decisiones y límites en `../catalog-contract.md`.

## Archivos

- `backend/app/modules/catalog/__init__.py` y `models.py`.
- `backend/migrations/versions/0030_catalog.py` y registro en `migrations/env.py`.
- `backend/tests/integration/test_catalog.py` y registro de modelos en `conftest.py`.
- Revisiones esperadas actualizadas a 0030 en `test_postgres.py`,
  `test_mfa_consumption.py` y `test_user_provisioning.py`; se conserva el objetivo
  histórico de downgrade de cada prueba.
- Contrato, estado de `data-model.md`, README y esta entrega.

## Validación ejecutada

Desde la raíz, los runners crean y eliminan su propio Compose/PostgreSQL efímero:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_catalog.py --test-path tests/integration/test_postgres.py
python3 -m scripts.validate_identity --test-path tests/integration/test_mfa_consumption.py --test-path tests/integration/test_user_provisioning.py
.venv/bin/ruff check --config backend/pyproject.toml backend/app/modules/catalog backend/migrations/env.py backend/migrations/versions/0030_catalog.py backend/tests/integration/conftest.py backend/tests/integration/test_catalog.py backend/tests/integration/test_postgres.py backend/tests/integration/test_mfa_consumption.py backend/tests/integration/test_user_provisioning.py
.venv/bin/ruff format --check --config backend/pyproject.toml backend/app/modules/catalog backend/migrations backend/tests/integration/test_catalog.py
git diff --check
```

Resultados reales: **22 + 110 pruebas pasan**, ambos runners terminan con código
0 y limpian sus recursos. Upgrade/check/downgrade/base/upgrade en PostgreSQL pasa;
Alembic check no detecta diferencias. Ruff y diff-check pasan. La primera llamada
sin elevación no pudo acceder a Docker; la ejecución autorizada anterior es la
evidencia válida. Logs privados: `.local/validation/catalog-3.2.log` y
`catalog-3.2-access-regression.log`.

Pruebas cubren SKU repetido entre empresas/colisión dentro de empresa incluyendo
inactivos, SKU opcional, categoría ajena, precio negativo/no finito/desbordado,
nombre/SKU inválidos, conservación al desactivar, restricción de borrado runtime,
FK de empresa y downgrade que conserva datos. Sin nueva ejecución del CI completo,
build frontend o navegador: no se modificaron sus recorridos.

## Límites y siguiente tarea

Migración aplicada solo en las bases efímeras de prueba; no en desarrollo ni VPS.
No hay endpoints, pantallas, lectura pública ni cambios de inventario habilitados.
Las FK no sustituyen autorización ni proporcionan RLS. PostgreSQL puede redondear
un precio con precisión extra: el contrato de escritura deberá rechazarlo antes.

Siguiente: servicio de alta/consulta de catálogo con sesión y empresa actuales,
validación de precio, permisos y auditoría atómica antes de conectar la interfaz.
VPS/TLS y restauración del despliegue anterior continúan pospuestos.
