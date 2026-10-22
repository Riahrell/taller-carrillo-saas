# 3.54 — Retirada de aplicaciones antiguas de la VPS

El 22 de octubre de 2026 el propietario solicitó borrar las aplicaciones y datos
de la VPS, confirmó que eran pruebas y que no había nada que conservar. Se omitió
un respaldo nuevo por esa instrucción explícita. Se preservaron sistema operativo,
acceso SSH y Docker; el Object Storage y la aplicación local quedan fuera del alcance.

## Recursos retirados realmente

- saas_frontend, saas_backend y saas_postgres.
- taller-carrillo_pgdata y taller-carrillo_postgres_data.
- frontend-taller-carrillo_default y taller-carrillo_default.
- Seis imágenes de aplicaciones/PostgreSQL/copias identificadas en el inventario.
- Caché de compilación del motor Docker que solo contenía estos proyectos.
- /opt/taller-carrillo y /opt/frontend-taller-carrillo, incluidos uploads locales.
- /var/backups/taller-carrillo y /var/lib/taller-carrillo-operations.

Las copias cifradas anteriores fuera de la VPS no se modifican: no son datos de
este servidor ni se crea una nueva copia. El borrado ejecutado es retirada lógica
de recursos/archivos, no un procedimiento de borrado criptográfico del disco.

## Protección y archivos completos

scripts/retire_legacy_vps.py comprueba las identidades exactas de contenedores,
proyectos, nombres y propiedad de volúmenes, redes, imágenes y rutas canónicas.
Rechaza recursos adicionales, identidades sustituidas, volúmenes remotos,
symlinks, montajes bajo los directorios y copias activas. No usa prune global de
contenedores/volúmenes ni llamadas S3. La caché se retira después de verificar que
no quedan contenedores ni imágenes. Por defecto es simulación y exige ambos
flags para aplicar; es una herramienta de una sola retirada, no reutilizable
para limpiar futuros despliegues.

Archivos cambiados:

- scripts/retire_legacy_vps.py
- scripts/tests/test_retire_legacy_vps.py
- docs/staging-plan.md
- docs/inventory-release-plan.md
- docs/deliveries/legacy-vps-retirement-3.54.md

## Comandos y resultados reales

```sh
python3 -m unittest scripts.tests.test_retire_legacy_vps
.venv/bin/ruff check scripts/retire_legacy_vps.py scripts/tests/test_retire_legacy_vps.py
.venv/bin/ruff format --check scripts/retire_legacy_vps.py scripts/tests/test_retire_legacy_vps.py
ssh -o BatchMode=yes -o ConnectTimeout=10 contabo python3 - < scripts/retire_legacy_vps.py
ssh -o BatchMode=yes -o ConnectTimeout=10 contabo python3 - --apply --discard-test-data < scripts/retire_legacy_vps.py
```

Tres pruebas aprobadas, con siete variantes de inventario inesperado y rechazo
de symlink/montaje anidado. Ruff correcto. La simulación coincidió con el inventario;
la aplicación terminó con ok=true. Recibo privado ignorado:
.local/validation/vps-retirement-3.54.json.

Verificación posterior desde una conexión SSH nueva:

- Cero contenedores, volúmenes, imágenes y caché (0 B).
- Ninguno de los cuatro directorios anteriores presente.
- Solo redes bridge, host y none; /opt conserva containerd del sistema.
- Docker activo y habilitado al arrancar; conexión SSH verificada.
- Puertos TCP en escucha: 22 y 53; dejaron de escuchar 3000, 5432 y 8000.
- Espacio libre final: 92 GiB, frente a 74 GiB del inventario inicial.

No se repiten suites de frontend o inventario: no cambia la aplicación. No se
instala la nueva versión, emiten certificados, cambian DNS, actualizan paquetes
ni reinicia el sistema. Sigue preparar configuración HTTPS nueva y despliegue
revisable, además de aceptación manual y respaldos de la aplicación nueva.
