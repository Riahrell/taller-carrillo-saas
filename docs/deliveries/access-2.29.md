# Acceso 2.29: inicio interno de login MFA

2026-10-17, America/El_Salvador. Sin integración HTTP/UI o activación pública.

## Contrato

`Authenticator.begin_mfa_login` reutiliza verificación Argon2id y cuotas de
contraseña compartidas con los métodos heredados. Solo admite credenciales,
binding SecretStr de 256 bits generado/verificado por servidor e IP validada del
proxy. No recibe usuario, tenant, rol, revisión, propósito o fechas del cliente.
Formato válido no acredita procedencia de la cookie: falta el adaptador HTTP.

Tras contraseña válida, usuario activo y al menos una membresía vigente, el
requisito global se sincroniza bajo lock: factor actual o membresía admin activa
en empresa activa lo hacen persistente. Cambiar de empresa/perfil o retirar el
rol no lo reduce. El avance cierra sesiones/pendientes de todas las empresas y
registra security_revision_advanced en ese commit. No se integra todavía el hook
de promoción/retirada o elegibilidad del último admin; el endurecimiento aquí se
aplica en este inicio interno. La entrada web heredada continúa sin llamar a él.

Resultado MFA: solo token opaco, propósito y caducidad. Factor actual exige TOTP
(cinco minutos); obligación sin historial de factor devuelve incorporación
restringida (quince). Contraseña sola no verifica buzón, activa semilla o permite
sesión. Historial de factor sin factor actual se rechaza: no volver a alta inicial
por correo; sustitución/recuperación siguen pendientes. Sin requisito/factor, se
conserva el login/selección existente. No se entregan empresas antes de MFA.

La hora de contraseña se captura justo después de comprobarla. Esperas, política,
rehash o reinicio de pantalla no amplían el plazo original de ese intento.
Repetir contraseña correcta es una verificación nueva, pero no reinicia el
presupuesto MFA. Limpia únicamente fallos de cuenta de contraseña, conserva IP
y todas las cuotas MFA. Reemplaza pendientes anteriores de ese usuario/navegador,
no los de otro navegador, salvo avance global de revisión. La revocación global
conserva su orden: buckets contraseña -> usuario -> selección -> pendientes -> sesiones.

Revocación, obligación, evento y pendiente son atómicos. Fallos de permisos/DB/
commit no publican token o estado parcial. Vencer durante la preparación revierte
la transacción. No se emiten códigos/semillas operativos, mensajes de correo o
rutas/cookies nuevas. El adaptador actual sigue rechazando cuentas con MFA exigido
hasta completar incorporación, recuperación y UI; no hay fallback a contraseña.

## Archivos completos modificados

- `backend/app/modules/access/authentication.py`: entrada interna y resultado restringido.
- `backend/app/modules/access/mfa_pending.py`: tiempo original y cierre por navegador.
- `backend/app/modules/access/security_state.py`: estado de integración en docstring.
- `backend/tests/integration/test_mfa_login_start.py`: pruebas con hashes reales y DB aislada.
- README, modelo, aceptación, decisiones y contratos/revisión de seguridad.

## Validación

Desde la raíz:

```bash
.venv/bin/python -m ruff check --config backend/pyproject.toml backend scripts
.venv/bin/python -m ruff format --config backend/pyproject.toml --check backend scripts
git diff --check
python3 -m scripts.ci > /tmp/carrillo-access-2.29-ci.log 2>&1
```

CI **exit 0**, proyecto `carrillo-ci-da6cc0919228`: **1.235 casos**,
1.040 backend (21 nuevos), 7 proxy, 152 frontend y 36 scripts. Lint/formato,
tipos, builds, migraciones/check, HTTP smoke y limpieza pasaron. Comprobación
final de Ruff/formato: 95 archivos, exit 0, incluida actualización documental
del kernel. Auditorías obligatorias passed/accepted; informe privado
`.local/security-reports/a4c4927e9d234a94874eb0a00a58829c.json`. Npm conserva
cuatro entradas SEC-2026-001; no se declara cero hallazgos.

Las 21 pruebas nuevas usan Argon2id/TOTP reales en DB efímera: recorrido interno
contraseña -> TOTP -> sesión, obligación global/admin de otra empresa, estados
inactivos, login sin requisito, democión, fallos reales de permisos/commit,
cuotas compartidas y no reinicio de MFA, credenciales inválidas, binding inválido,
reinicio por navegador, plazo original tras trabajo/espera, rehash, historial
revocado y dos workers realmente bloqueados que endurecen revisión una sola vez.
Sin recorrido MFA público de navegador o ensayo de caída física de COMMIT.

Build, arranque/recarga, smoke, current/check y consulta de servicios: exit 0.
Head `0015_mfa_sessions`, check sin nuevas operaciones y cuatro servicios
saludables. Smoke comprobó HTML español, 10 assets, mismo origen, 404,
cabeceras y rechazo sin Origin/CSRF. Sin migración/backfill ni ejecución del
nuevo inicio sobre cuentas reales. Registro `/tmp/carrillo-access-2.29-local.log`.

## Ejecución local

Sin migración nueva: se reconstruye backend y se verifica 0015. No se ejecuta
el nuevo inicio interno sobre cuentas reales; no cambia políticas/contraseñas
o sesiones operativas por desplegar esta entrega. Desde la raíz:

```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml build backend > /tmp/carrillo-access-2.29-dev-build.log 2>&1
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml up -d --wait backend frontend proxy
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml exec -T proxy nginx -s reload
python3 scripts/smoke_web.py --base-url http://localhost:8080
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic current
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml run --rm migrate python -m alembic check
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml ps
```

## Riesgos y siguiente tarea

Sin cambio de esquema: sigue 0015. MFA aún sin HTTP/UI, incorporación/verificación
de buzón, recuperación, reautenticación MFA, hooks/último admin, cancelación HTTP,
retención, HTTPS y operación de claves completos. La excepción SEC-2026-001 npm
vence 2026-10-31 00:00 UTC, sin ampliación. No preparación productiva o certificación.
Siguiente 2.30: desafíos de correo para incorporación MFA restringida.
