# Pruebas y CI

## Validación completa

Desde la raíz:

```bash
python3 -m scripts.ci
```

Necesita Docker y acceso a los registros de imágenes y avisos de dependencias.
Genera un proyecto `carrillo-ci-*` con credenciales independientes, PostgreSQL
efímero y etiquetas de imágenes por ejecución. No usa la base local de desarrollo
ni la VPS. Al terminar intenta eliminar los recursos de ese proyecto temporal.

El recorrido comprende:

1. Pruebas de scripts y controles de secretos/dependencias.
2. Construcción de contenedores y validación de los workflows.
3. Ruff, ESLint, formato, TypeScript y pruebas frontend.
4. Migraciones, pruebas backend y PostgreSQL real.
5. Integraciones por Nginx/Uvicorn y smoke HTTP.
6. Informes de imágenes de ejecución y SBOM.

El [workflow](../.github/workflows/ci.yml) usa el mismo comando en push, pull request
y ejecución manual. Sus permisos declarados son de lectura del repositorio;
esta validación no despliega automáticamente la aplicación.

## Comprobaciones dirigidas

Pruebas de herramientas, sin desplegar:

```bash
python3 -m unittest discover -s scripts/tests
```

Una integración backend en una base aislada:

```bash
python3 -m scripts.validate_identity \
  --test-path tests/integration/test_postgres.py \
  --test-timeout 300
```

Herramientas frontend locales, con Node.js 24; ejecutar desde `frontend/`:

```bash
npm ci
npm run lint
npm run format:check
npm run typecheck
npm test
```

Estas últimas comprobaciones no sustituyen las integraciones con base y proxy.
Selecciona las comprobaciones adecuadas para el cambio; una modificación de
documentación necesita revisar enlaces, ejemplos y secretos, no alterar datos.

## Entender un fallo de GitHub Actions

Abre la ejecución y localiza el **primer paso que falla**. El último `exit code 1`
solo indica que el comando terminó con error.

| Salida                                   | Interpretación                                                                      |
| ---------------------------------------- | ----------------------------------------------------------------------------------- |
| Ruff `E501`, lint o formato              | La validación de estilo falló.                                                      |
| Vitest/Pytest con aserciones fallidas    | Revisar las pruebas y el comportamiento que esperaba el caso.                       |
| Timeout                                  | Se agotó el presupuesto; no acredita que las aserciones pendientes hubieran pasado. |
| Hallazgos marcados `accepted`            | Revisar la política de excepción y su vigencia; no deducir que causaron otro fallo. |
| Ejecución `cancelled` tras un nuevo push | Puede corresponder a `cancel-in-progress` del workflow.                             |

Una ejecución fallida no significa necesariamente que el push haya sido rechazado.
Los fallos históricos permanecen en Actions después de corregir el código.

## Seguridad y evidencias

El escaneo busca secretos en archivos versionados y en el historial. Las
excepciones de dependencias son explícitas y caducan. El archivo actual incluye
`SEC-2026-001` con vencimiento el 31 de octubre de 2026: requiere revisión y no
debe ampliarse solo para obtener CI verde.

No incorpores logs completos, `.local/`, credenciales, cookies ni bases de datos
como evidencia. Guarda resultados de versión y tipo de comprobación; no publiques
tokens o valores detectados por un escáner.

Los resultados de entregas anteriores son históricos. Antes de anunciar un cambio
como validado, revisa el resultado de su commit y distingue pruebas locales de
ejecuciones remotas. Consulta también [Estado y versiones](project-status.md).
