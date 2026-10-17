# Persistencia y consumo interno MFA: acceso 2.22

Implementación parcial del [contrato MFA](mfa-recovery-contract.md). Migración
`0009_mfa_credentials`, sin backfill, endpoints, configuración de claves ni
activación en login. Los únicos factores creados durante la validación pertenecen
a identidades sintéticas en PostgreSQL efímero; no crear factores operativos por
SQL o CLI mientras falten las comprobaciones de todos los caminos de sesión.

## Modelo físico de esta entrega

| Tabla | Datos y restricciones |
| --- | --- |
| `mfa_credentials` | Usuario global, UUID de factor, versión AEAD, key id, nonce, ciphertext, creación, revocación y par último contador/hora aceptados. Solo un factor no revocado por usuario mediante índice único parcial. |
| `mfa_recovery_sets` | Usuario y factor ligados por FK compuesta, creación e invalidación. Solo un conjunto no invalidado por factor mediante índice único parcial. |
| `mfa_recovery_codes` | Conjunto, posición 0–9, hash de 32 bytes y consumo nullable. PK conjunto/posición y hash único por conjunto. No contiene texto del código. |

Una credencial representa un factor confirmado. El futuro pendiente de
incorporación guardará su propia semilla cifrada antes de confirmar; no se añade
un factor sin verificar ni una marca de correo verificado en esta migración.
El par de consumo nullable permite representar ausencia de consumos, pero no
acredita confirmación: la futura activación deberá verificar y almacenar el
contador/hora de su primer OTP en el mismo commit, sin dejarlo reutilizable.
No hay tenant, rol, precio o stock en estas tablas globales. Credenciales/conjuntos
revocados conservan historia; el servicio rechaza cualquier revocación/invalidez
no nula, aunque su fecha esté en el futuro.

Los CHECK de credencial exigen versión 1, key id de formato limitado, nonce de
12 bytes, ciphertext de 36 y coherencia del par contador/hora (ambos nulos o
contador no negativo/hora no anterior a creación). Fechas de revocación/invalidez
no pueden anteceder la creación. Los códigos solo permiten posiciones 0–9 y
hash de 32 bytes; la futura activación debe insertar **exactamente diez** en un
commit. La posición acota el máximo, no garantiza por sí sola diez filas ni la
entropía del material. Estos CHECK no sustituyen la autenticación AEAD.

## Contrato de consumo

`mfa_consumption.consume_totp` y `consume_recovery_code` son funciones internas
de bajo nivel, sin commit propio. El llamador confiable obtiene IDs de estado
validado en el servidor, abre la transacción y confirma o revierte todo. No
entregar `MfaConsumption` como prueba transferible ni publicar éxito antes del
commit. Tampoco llamar estas funciones directamente desde una ruta nueva sin
pendiente válido, contraseña, cuota y auditoría de seguridad.

Orden: usuario con FOR UPDATE y actividad releída -> credencial de ese usuario
con FOR UPDATE -> conjunto -> código, cuando corresponda. Todos los consumidores
del mismo factor comparten contador, aunque luego existan distintos propósitos
HTTP. La consulta refresca objetos ORM previamente cargados (`populate_existing`)
y lee `clock_timestamp()` después de las esperas, evitando la hora del comienzo
de una transacción bloqueada. No recibe hora, rol o un flag de verificación del
cliente. No toma locks de empresas después de estos locks globales.

TOTP descifra con usuario/credencial como AAD, verifica el perfil 2.21 y solo
actualiza contador/hora si existe una coincidencia única posterior al contador
consumido. La misma transacción mantiene locks hasta commit/rollback. El tiempo
regresivo respecto a la verificación previa se rechaza incluso subsegundo.

Recuperación busca únicamente en el conjunto/usuario/factor esperados y vigentes,
calcula el hash con ese usuario/conjunto y marca una sola fila sin usar. Los demás
códigos permanecen intactos. Cambiar contraseña no modifica esos hashes. Consumir
un código **no sustituye factor, no abre recuperación ni emite sesión** en esta
entrega: esos efectos necesitan el flujo restringido autorizado posterior.

Identidad inexistente, inactiva, revocada o ajena, código inválido/repetido y
fecha imposible devuelven ausencia de consumo; eso nunca autoriza una sesión
sin MFA. Claves ausentes/tags inválidos fallan con `SeedProtectionError` genérico,
sin desactivar factores. Errores SQL/flush/commit se propagan y exigen rollback;
no hay reintento automático de una transacción de resultado incierto.

La app tiene SELECT/INSERT/UPDATE en estas tablas, **sin DELETE**; TRUNCATE/DDL no
se conceden. El propietario de migraciones es otra identidad. No se ofrece
defensa frente a SQL arbitrario con UPDATE autorizado o al operador propietario:
los servicios/secretos y privilegios operativos siguen siendo una frontera.

## Migración y reversión

Upgrade solo crea tablas/restricciones y ajusta permisos de las tablas nuevas;
no cambia cuentas, sesiones, contraseñas o datos comerciales. Alembic check debe
coincidir con metadata, y el recorrido vacío base -> head -> base -> head funciona.

Downgrade toma locks exclusivos y rechaza si hay **cualquier credencial**, incluso
revocada. Las FK RESTRICT impiden conjuntos/códigos huérfanos, por lo que esa
comprobación también conserva consumos. No borrar filas para forzar un rollback
ni bajar a una versión que ignore factores activos. Con datos reales, corregir
hacia adelante y preparar un procedimiento explícito de recuperación/mantenimiento.

## Límites y siguiente paso

Todavía faltan: auditoría global append-only, cuotas MFA compartidas, obligación
admin/revisión de seguridad, pendientes y prueba de buzón, activación/sustitución,
recuperación restringida, revocación global, guardias de emisión/resolución y UI.
Esta entrega tampoco configura custodia/rotación operativa de claves o monitoreo
de reloj/restauración; preservar códigos en DB no prueba un backup seguro.

Siguiente tarea pequeña **2.23**: registro global de seguridad append-only para
consumos MFA, con evidencia en el mismo commit y pruebas de privilegios/rollback.
Las cuotas y la integración completa siguen siendo necesarias antes de activarlo.

Fuentes técnicas consultadas el 2026-10-17:
[locks de PostgreSQL](https://www.postgresql.org/docs/18/explicit-locking.html#LOCKING-ROWS)
y [refresco ORM de SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing).
La decisión concreta de orden de locks y los criterios son del proyecto;
consultar estas fuentes no equivale a certificar la integración completa.
