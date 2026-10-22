# 3.45 — Salidas autorizadas y revisión de tecnologías

## Resultado funcional

El administrador puede conceder a Cajero consulta y salidas manuales desde
`/permisos`. El empleado usa `/inventario/consulta`, revisa cantidad/motivo y
confirma. No registra ventas/cobros ni recibe costos. Solo lectura no escribe;
las entradas, ajustes y anulaciones siguen administrativos.

La salida reutiliza la valoración de servidor y la transacción de movimientos,
evidencia y saldo. Exige concesión vigente, producto activo propio, contexto,
CSRF/origen e idempotencia ligada además al autor. Retirar el permiso impide
nuevas salidas y repeticiones; desactivar o cambiar perfil retira la concesión.

## Archivos cambiados

- Backend: `access/inventory_permission_models.py`, `inventory_permissions.py`,
  `inventory_permission_routes.py`, `administration.py`, `models.py`, `main.py`;
  nuevo `inventory/withdrawals.py`, adaptación `manual.py` y `routes.py`.
- Migración nueva `0046_inventory_withdrawal_grants.py`, evidencia append-only
  y downgrade no destructivo; actualización de expectativas de head en pruebas.
- Frontend: nuevo formulario `inventory-withdrawal-form.tsx` y cliente
  `lib/inventory/withdrawals.ts`; integración en consulta, permisos y acceso;
  ampliación del cliente de concesiones sin cambiar el permiso Solo lectura.
- Pruebas nuevas de autorización, HTTP, valoración, concurrencia, idempotencia,
  rollback, entrada estricta, revocación y revisión/confirmación de interfaz.
- Seguridad: Dockerfiles frontend/backend/auditoría, PostgreSQL y Nginx,
  lock de herramientas Python y `scripts/security.py`
  fijan Node 24.22.1 y Python 3.13.17 por digest oficial, pip 26.2.2 con hashes
  PyPI y musl/musl-utils 1.2.6-r3 en infraestructura.
- Contratos y reporte `docs/security-stack-review-2026-10-21.md`; actualización
  de matriz de permisos y plan de inventario.

## Comandos de validación

Desde la raíz:

```bash
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_withdrawal_permissions.py --test-path tests/integration/test_inventory_permissions.py --test-path tests/integration/test_manual_stock.py --test-path tests/integration/test_membership_administration.py --maxfail 3
python3 -m scripts.validate_identity --test-path tests/integration/test_inventory_withdrawal_permissions.py --maxfail 2
python3 -m scripts.security
.venv/bin/python -m pytest scripts/tests/test_security.py scripts/tests/test_image_security.py -q
.venv/bin/ruff check backend/app backend/migrations/versions/0046_inventory_withdrawal_grants.py backend/tests/integration/test_inventory_withdrawal_permissions.py scripts/security.py
git diff --check
```

Imágenes y frontend con las versiones corregidas:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build backend frontend frontend-tests
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml build db proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm --no-deps frontend-tests
python3 -m scripts.image_security --image taller-carrillo-postgres:dev --image taller-carrillo-proxy:dev
python3 -m scripts.image_security --image taller-carrillo-backend:dev --image taller-carrillo-frontend:dev
```

Resultados reales: 73 pruebas backend seleccionadas aprobadas con Python 3.13.17;
341 pruebas frontend en 27 archivos aprobadas dentro de Node 24.22.1, junto con
ESLint, Prettier y tipos. Build de backend/frontend/herramientas aprobado. Pruebas
de los escáneres: 27 aprobadas y 20 subtests. Ruff y `git diff --check` correctos.

La primera validación ampliada detectó un error de limpieza de datos del fixture
HTTP que contaminaba pruebas posteriores; se corrigió el teardown manteniendo
las FK y la suite final de 73 pasó. El formato del archivo nuevo también se
ajustó a la versión fijada de Prettier antes de aprobar el contenedor de pruebas.

Escaneo de las imágenes finales backend/frontend: cero hallazgos conocidos,
con SBOM y evidencia privada `.local/image-reports/e4dabe04c3a44ea9a337b5d8d3e7475d`.
El primer escaneo de PostgreSQL/proxy identificó musl/musl-utils 1.2.6-r2,
CVE-2026-107891 (severidad UNKNOWN), y se aplicó 1.2.6-r3 antes de la revisión final.
La actualización de pip se comprobó contra los hashes publicados por PyPI;
el lock de desarrollo anterior reinstalaba pip 26.2.1 sobre la nueva imagen.
Después de corregir ese lock y musl de PostgreSQL, las 15 pruebas del archivo
nuevo volvieron a pasar con la instalación final (son parte de las 73 anteriores,
no se suman como pruebas distintas).

Reescaneo PostgreSQL/proxy: cero hallazgos conocidos, evidencia privada
`.local/image-reports/519ccf29245041428956e9a786917971`. Los cuatro runtime
escaneados quedan sin hallazgos conocidos con esa base de vulnerabilidades.
El audit de locks final mantiene cero hallazgos Python y cuatro npm aceptados
por la excepción existente; secretos de archivos/historial, sin hallazgos.

Los logs privados quedan ignorados en `.local/validation/inventory-withdrawal-*`.

## Actualización local

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait backend frontend proxy identity-worker image-worker
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml up -d --no-deps --wait db proxy
python3 scripts/smoke_web.py --base-url http://localhost:8080
```

Los workers existentes se actualizan por el parche de Python; conservan el alcance
de correos solicitados en la aplicación. No se generan correos de prueba ni se toca VPS.

Web local actualizada y smoke HTTP aprobado. Versiones activas confirmadas:
Node 24.22.1, Python 3.13.17 y Alembic `0046_inventory_withdrawal_grants (head)`.
La base de datos conservó su volumen. Los workers existentes se actualizaron;
no se emitieron mensajes de prueba desde esta entrega.

## Prueba manual

1. Administrador: si corresponde, cambia el perfil del empleado a Cajero en
   Membresías. Eso revoca su sesión anterior y requiere un nuevo inicio de sesión.
2. En Permisos, selecciona ese cajero, marca permitir consultar y registrar
   salidas, revisa y confirma.
3. Empleado: abre Consultar existencias, selecciona Registrar salida, introduce
   cantidad positiva/motivo, revisa y confirma una vez. Comprueba la nueva cantidad.
4. Administrador: verifica el movimiento en el historial privado. Retira el permiso;
   una nueva operación del empleado debe bloquearse.
5. Comprueba que Solo lectura no tiene botones de salida y que el cajero no accede
   a costos, ajustes, entradas ni administración.

No se ejecutó una prueba manual con la sesión del usuario en navegador.

## Riesgos y siguiente tarea

- Pendiente mantenimiento del host: Docker 29.9.2 y Compose 5.6.1; versiones
  locales observadas 29.9.1/5.6.0, con parche de certificados publicado.
- Cuatro hallazgos npm exclusivos de desarrollo conservan la excepción existente
  SEC-2026-001 hasta el 31 de octubre. No se declara todo el stack libre de fallos.
- Una respuesta de salida perdida exige revisar saldo/historial antes de repetir;
  no hay reintentos automáticos ni recuperación persistente del comprobante.
- Entradas y ajustes de empleados, pendientes de registro y cierre operativo VPS
  siguen fuera de esta entrega. Primero cerrar mantenimiento de seguridad del host
  y aceptación manual; después definir la concesión separada para entradas/costos.
