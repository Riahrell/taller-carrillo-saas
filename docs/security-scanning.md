# Escaneos de seguridad: acceso 2.19

Fecha: 2026-10-17. Ejecutar desde la raíz: `python3 -m scripts.security`.
Se integra obligatoriamente en `python3 -m scripts.ci`, antes de compilar/probar
la aplicación. No modifica cuentas, esquema, lockfiles de aplicación o datos.

## Alcance y aislamiento

Gitleaks **8.30.1**, imagen oficial fijada por digest, revisa el historial completo
de referencias locales mediante un mirror temporal sin hardlinks, y una copia
del árbol de trabajo. Esta incluye archivos tracked con cambios y nuevos no
ignorados. Excluye credenciales locales ignoradas; rechaza directorios locales
forzados, symlinks, submódulos e historial shallow. No revisa reflogs, objetos
inalcanzables, ramas que nunca se descargaron o archivos ignorados fuera de Git.
Checkout CI usa fetch-depth 0 y mantiene persist-credentials false.

Las reglas por defecto permanecen activas. No se admiten comentarios
`gitleaks:allow`, fichero .gitleaksignore o baseline que silencien hallazgos.
Una prueba automática introduce un token sintético en un repositorio efímero,
lo detecta en archivo y después de borrarlo en otro commit, y comprueba redacción.
Esto prueba el funcionamiento del detector, no cobertura perfecta de secretos.

pip-audit **2.10.1**, con dependencias en lockfile separado y hashes, revisa
requirements.lock de runtime, desarrollo y de la herramienta. Usa
--disable-pip, --no-deps, --require-hashes y --strict: no resuelve, instala o
ejecuta paquetes de la aplicación. npm **11.19.1**, incluido en la imagen Node 24
fijada del proyecto, revisa package-lock.json con desarrollo incluido,
--package-lock-only y --ignore-scripts. No ejecuta audit fix.

Contenedores no privilegiados, raíz read-only, sin capabilities, sin socket
Docker y con memoria/CPU/procesos acotados. Secretos se revisan sin red. Los
auditores con red reciben solo manifiestos/lockfiles; nunca código, Git, reportes
del detector o .local. Si las revisiones de secretos fallan o encuentran algo,
se bloquean las consultas de dependencias. Se eliminan los contenedores propios
que puedan quedar tras timeout y la etiqueta temporal de la herramienta.

PyPI y el registro oficial npm reciben metadatos de las dependencias públicas
para consultar vulnerabilidades. No hay claves de proveedor/servicio ni env de
la aplicación en esos contenedores. No extrapolar este contrato a dependencias
privadas futuras sin revisar qué metadatos se transmitirían.

## Resultado y política

Estados diferenciados: passed, findings, accepted y error. Exit 0 exige cinco
etapas completas y solo passed/accepted; exit 1 indica hallazgos no aceptados;
exit 2 indica revisión incompleta/error. Un fallo de red, dependencia omitida,
JSON inválido o estado inconsistente nunca se convierte en resultado limpio.
CI falla ante cualquiera de los dos códigos no cero. Un bloqueo de seguridad
impide construir/arrancar la aplicación de pruebas.

Los reportes saneados quedan en .local/security-reports con permisos privados:
fecha, commit base, cantidad de archivos del worktree, hashes de entradas,
imágenes fijadas, estado y metadatos mínimos de hallazgos. El commit base no
significa que el árbol estuviera limpio: la copia incluye sus cambios actuales.
No publicar snippets, valores de secretos, autores o correos de commits. Las
salidas crudas no se imprimen; los reportes temporales de Gitleaks usan redacción
total y se eliminan. No se suben artefactos automáticamente a terceros.

La política por defecto bloquea cualquier aviso de dependencias o secreto
detectado. Las excepciones solo aplican al aviso npm y versiones/ubicaciones
dev-only expresamente enumeradas en ops/security/exceptions.json. Máximo 14 días,
responsable, motivo y fecha obligatorios. El hallazgo queda visible como accepted,
no pasa a cero avisos. Cambiar versión, usar la cadena en runtime, recibir otro
aviso o vencer obliga a revisión; no hay renovación automática.

## Hallazgo SEC-2026-001

El primer escaneo detectó **un aviso raíz y cuatro entradas npm de severidad alta**:
@next/eslint-plugin-next 16.4.2 → fast-glob 3.3.1 → micromatch 4.0.8 → braces 3.0.3.
GHSA-vfj7-8cjw-p6xm / CVE-2026-93687 describe agotamiento de pila con patrones de
llaves anidados; el aviso oficial y el registro consultados no ofrecen versión
corregida de braces. No son cuatro vulnerabilidades independientes.
[Aviso oficial](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).

Decisión técnica: aceptar temporalmente esta cadena dev-only hasta **2026-10-31
00:00 UTC**, conservando ESLint/Next y el aviso visible. package-lock clasifica los
cuatro paquetes como dev. Se inspeccionó get-root-dirs del plugin instalado:
fast-glob se usa con settings.next.rootDir; nuestra configuración no define esa
opción y usa cwd. No recibe patrones de peticiones del taller. Es evidencia de
exposición limitada en este uso, no prueba universal de inalcanzabilidad.

La alternativa automática propuesta por npm reduce el plugin a 14.2.35, fuera
de la línea Next 16 del proyecto. No se aplicó ese downgrade, se retiraron reglas,
parcheó una dependencia a escondidas o se ocultó la severidad. La excepción exige
versiones exactas, dev=true, ubicación conocida y el mismo aviso. Otra aparición
anidada o de severidad crítica no queda cubierta. Es una aceptación técnica
limitada al desarrollo, no autorización para publicar producción.

Al revisar: comprobar proveedor/registro, instalar y probar una corrección
compatible si existe y retirar la excepción. Si no existe, evaluar sustitución
conservando los controles y documentar una nueva decisión; no extender fecha
mecánicamente. Antes del vencimiento CI ya muestra el hallazgo en cada ejecución;
después se bloquea. No hay monitor o recordatorio programado por esta entrega.

## Límites y siguientes controles

Estos escaneos consultan vulnerabilidades conocidas; sus resultados cambian con
las bases de avisos aunque el lockfile no cambie. Fijar herramienta no congela
los avisos. Necesitan internet para esas consultas y para descargas no cacheadas.
No usar --ignore, --audit-level elevado o continue-on-error para trabajar offline;
se puede seguir desarrollando localmente, dejando la validación pendiente.

No cubren CVEs de paquetes del sistema operativo/imágenes, análisis estático
completo, licencias/SBOM, pruebas de penetración, diseño comercial, restauración
de backups o evaluación ASVS. GitHub CI remoto todavía no fue ejecutado: no hay
repositorio remoto configurado. Contrato MFA/recuperación definido en 2.20:
`mfa-recovery-contract.md`. Próxima tarea 2.21: biblioteca interna TOTP/cifrado,
con nuevas dependencias auditadas; email-recovery-design.md sigue sin implementar.

Referencias oficiales consultadas el 2026-10-17:

- [Gitleaks](https://github.com/gitleaks/gitleaks): modos Git/dir, redacción y CLI.
  Proyecto feature complete con futuras actualizaciones de seguridad; revisar
  mantenimiento al actualizar su versión fijada.
- [pip-audit](https://github.com/pypa/pip-audit): contratos de auditoría y salida.
- [npm audit](https://docs.npmjs.com/cli/commands/npm-audit/): auditoría del lockfile.
