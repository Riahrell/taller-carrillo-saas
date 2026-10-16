# Entrega: sesiones opacas revocables (acceso 2.3)

Fecha: 2026-10-16 (America/El_Salvador).
Rama: `codex/access-sessions`.

## Resultado

Tabla sessions y servicios internos de emisión, resolución y revocación individual,
por membresía y por usuario. El servidor genera tokens aleatorios de 256 bits y
almacena solo su hash SHA-256; el token se devuelve como SecretStr. No se acepta
un token elegido por quien llama a emisión, ni tenant/rol al resolver una sesión.

Sesión vinculada por FK compuesta al tenant y membresía. Contexto y rol se leen
desde DB, comprobando empresa, usuario y membresía activos. La sesión expira al
alcanzar exactamente 30 minutos sin actividad o ocho horas desde creación. La
actividad válida solo actualiza last_seen_at, sin prolongar el límite absoluto.

Reloj PostgreSQL clock_timestamp() leído tras esperar los bloqueos. Orden común:
usuario FOR UPDATE y después sesiones por UUID FOR UPDATE. Evita reactivación por
actualizaciones de actividad concurrentes con revocación y coordina la emisión
con revocación global. Todas las operaciones usan la transacción del llamador:
no hacen commit ni rollback propios y el token no debe publicarse antes de commit.

No hay nuevos endpoints, cookies ni login. La emisión interna requiere identidad
ya verificada bajo bloqueo del usuario en esa misma transacción. No se creó una
cuenta ni una sesión real en desarrollo durante las verificaciones.

## Archivos cambiados

- `backend/app/modules/access/models.py`: SessionRecord, FK compuesta, índices
  y restricciones de hash, fechas y duración.
- `backend/app/modules/access/sessions.py`: servicios internos y generación/hash
  de tokens; bloqueos, reloj DB y renovaciones/revocaciones transaccionales.
- `backend/migrations/versions/0003_sessions.py`: upgrade/downgrade explícitos.
- `backend/tests/test_session_tokens.py`: cinco casos de formato inválido que no
  consultan DB, con error genérico y logout interno idempotente.
- `backend/tests/integration/test_sessions.py`: 20 casos de persistencia, límites,
  aislamiento, revocación, rollback y restricciones SQL.
- `backend/tests/integration/test_session_concurrency.py`: tres casos con conexiones
  independientes y observación real de espera en pg_locks.
- `backend/tests/integration/test_postgres.py`: revisión esperada 0003_sessions,
  manteniendo el ciclo upgrade/check/downgrade/upgrade en DB efímera.
- `backend/tests/integration/test_bootstrap_postgres.py`: restaura la revisión head
  vigente después de probar rechazo de esquema antiguo, sin fijarla a 0002_access.
- `README.md`, `docs/decisions.md`, `docs/data-model.md`, `docs/acceptance.md`
  y este registro.

No se cambiaron dependencias ni archivos de bloqueo.

## Comandos exactos ejecutados

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --check --config backend/pyproject.toml backend scripts
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -m 'not integration'
python3 -m scripts.ci
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic -c alembic.ini check
python3 scripts/smoke_web.py --base-url http://127.0.0.1:8080
git diff --check
```

El runner CI y Compose usan secretos ya generados o efímeros sin imprimirlos.
Para una instalación nueva, seguir primero la generación del entorno y arranque
DB del README. El downgrade destructivo se prueba únicamente en DB efímera.

## Resultados reales

- CI local integral `carrillo-ci-c302fe0471bb`: **exit 0, CI passed**.
- **116 pruebas backend aprobadas en 1.44s**, incluidas **56 PostgreSQL**.
- **15 frontend** y **6 del ejecutor** aprobadas: **137 pruebas en el pipeline**.
- Unitarias locales: **60 aprobadas en 0.51s**, 56 integración excluidas en ese
  comando. Son los mismos casos unitarios del pipeline, no un total adicional.
- Ruff check correcto; formato correcto en **33 archivos**. `pip check` correcto.
  actionlint, ESLint, Prettier y tipos correctos. Builds completados con caché
  donde correspondía, sin afirmar construcción sin caché.
- Migración en DB efímera, check de metadata y roundtrip de migraciones correctos.
- En desarrollo: **0003_sessions (head)** y Alembic check **No new upgrade
  operations detected**. Cuatro servicios saludables; ningún borrado de datos.
- Smoke CI y local correctos: HTML español, nueve assets, API mismo origen,
  404 de interfaz/API y cabeceras. La interfaz inicial sigue sin login.
- Concurrencia real: resolver/revocar en ambos órdenes termina con token rechazado
  y revoked_at persistido; emisión en curso seguida de revocación de usuario
  incluye el token recién confirmado. Se observó la conexión contendiente bloqueada
  en pg_locks antes de liberar al titular; no se dedujo concurrencia por un sleep.
- Los límites exactos y la renovación acumulada se prueban sustituyendo el helper
  privado de reloj por instantes deterministas, con filas/restricciones PostgreSQL
  reales. Los otros casos y las carreras usan el reloj PostgreSQL sin sustituirlo.
- Rollback del llamador revierte emisión, actividad, cambio de rol y revocación;
  token emitido dentro del rollback queda desconocido. Revocación por membresía
  conserva sesiones del usuario en otra empresa; por usuario revoca ambas y
  conserva la sesión de otra identidad.
- FK rechaza tenant ajeno; CHECK rechaza digest no canónico, ampliación de vida,
  actividad fuera de intervalo y revocación anterior a creación. Hash duplicado
  y borrado de membresía referenciada rechazados también por PostgreSQL.
- El ejecutor retiró recursos y secretos temporales CI al terminar. Desarrollo
  conserva su volumen y credenciales. No se hizo push ni despliegue público.

## Riesgos y límites

El hito 2 sigue abierto: faltan autenticación de credenciales con límites de intentos
compartidos, contratos HTTP, cookies seguras, CSRF, protección de origen, permisos
por operación, auditoría y frontend de acceso. No usar en producción.

issue_session es un servicio interno, no un login: conocer un UUID no acredita
identidad. El futuro login debe verificar la contraseña bajo el mismo bloqueo de
usuario y confirmar antes de entregar cookies/tokens. Ningún contrato público
puede permitir que el cliente invoque emisión con IDs de su elección.

Los cambios de contraseña deben usar revoke_user_sessions y los de privilegios
revoke_membership_sessions en su misma transacción. No hay todavía APIs para esos
cambios ni triggers que revoquen por SQL manual. Cada operación futura debe
respetar el orden de locks; las operaciones con varios usuarios los bloquearán en
orden estable de UUID antes de sus sesiones. No conservar un contexto entre
peticiones ni esperar llamadas externas con los bloqueos adquiridos.

La serialización por usuario prioriza consistencia y puede limitar paralelismo de
sus peticiones. Mantener transacciones cortas; límites de espera/carga y medición
operativa se definirán antes de exposición. Las transacciones de inventario/caja
requerirán además autorización y bloqueos de sus propias entidades.

No hay RLS: el rol DB de aplicación conserva DML general. La FK protege referencias
entre empresas, pero no protege de un operador que reescriba directamente IDs o
registros. La comprobación de formato SHA-256 no demuestra origen aleatorio;
el token seguro lo genera el servicio. No registrar tokens en claro.

Las sesiones expiradas no se borran automáticamente; retención y limpieza quedan
pendientes. El reloj del servidor DB debe estar sincronizado. No se agregaron
límites de sesiones simultáneas ni IP/agent del navegador sin una necesidad definida.
GitHub Actions remoto sigue pendiente porque no hay remoto configurado.

## Siguiente tarea

Acceso 2.4: autenticación de credenciales con límites compartidos de intentos en
PostgreSQL y contrato HTTP/cookies/CSRF antes de habilitar login. Mantener la
entrega incremental; después conectar el formulario de acceso y probar el flujo.
