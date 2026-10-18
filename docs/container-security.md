# Imágenes de ejecución y SBOM

El escaneo analiza paquetes de las imágenes Docker, no las fotografías ni los
archivos de Contabo. No limpia la VPS ni modifica contenedores operativos.

`python3 -m scripts.ci` construye y valida las imágenes de backend, frontend,
PostgreSQL y proxy con nombres únicos por ejecución. Al final invoca
`python3 -m scripts.image_security --image REFERENCIA` para cada una. También se
puede ejecutar ese comando por separado, repitiendo `--image` para imágenes ya
construidas. No descarga imágenes de aplicación implícitamente.

El proceso fija Trivy 0.76.0 por digest OCI. Descarga únicamente la base oficial
sin montar imágenes, código o credenciales. Después exporta imágenes por ID
inmutable y las analiza con red deshabilitada, filesystem de contenedor de solo
lectura, usuario del operador, sin capacidades ni socket Docker. Se admite
escritura solo en tmpfs, caché en memoria e informes. La base debe tener versión
2, actualización de las últimas 48 horas y no más de cinco minutos en el futuro.
La frescura y SHA-256 de la base quedan en el resumen de cada ejecución.

Se conserva inventario completo en JSON y CycloneDX dentro de
`.local/image-reports/ID/`, protegido por el directorio privado y excluido de Git.
El resumen identifica cada imagen, su ID, componentes y hallazgos. Los archivos
son evidencia técnica de la imagen construida, no una certificación ni sustituto
de pruebas de autorización, consistencia o restauración.

El comando devuelve 0 si todas las imágenes fueron analizadas y no presentan
hallazgos HIGH/CRITICAL, 1 si los hay y 2 si el análisis quedó incompleto. No oculta
hallazgos sin parche, no usa ignorefiles del repositorio ni aplica la excepción
npm a las imágenes de ejecución. Los hallazgos menores también se conservan.
Una imagen sin paquetes, sin OS reconocible o fuera de soporte no pasa. Un fallo
de conectividad/herramienta no equivale a un resultado limpio.

## Remediación del 18 de octubre de 2026

El primer análisis identificó 142 hallazgos HIGH/CRITICAL entre las cuatro
imágenes (no equivalen a 142 vulnerabilidades distintas). Se conservaron los
informes iniciales; no se crearon excepciones para hacerlos pasar.

- Backend y frontend usan bases Alpine oficiales fijadas por digest. Los locks
  incluyen los wheels y paquetes nativos necesarios; la compatibilidad debe
  quedar demostrada con build y regresión completa.
- `pip` y `npm` permanecen en las etapas de construcción/pruebas, pero se retiran
  de las imágenes desplegadas. La aplicación no instala paquetes al arrancar.
- PostgreSQL conserva la misma versión mayor y scripts oficiales. Arranca como
  UID 70 directamente; se elimina `gosu`, que solo sirve para descender desde
  root y contenía una biblioteca Go antigua. No se elimina ni omite el inventario
  del escáner. La inicialización de roles, permisos, migraciones y restauración
  debe probarse con ese usuario. Volúmenes anteriores con propietarios diferentes
  necesitan una migración operativa revisada; no ejecutar chown global.
- El proxy incorpora libexpat 2.9.0-r0 y, desde inventario 3.5, libx11 1.8.14-r0
  y libxpm 3.5.20-r0. El escaneo con base del 2026-10-18T18:32:29Z detectó
  nuevos avisos en las versiones anteriores: se aplican paquetes corregidos de
  la distribución, sin excepción de runtime. Las cuatro imágenes incorporan zlib
  1.3.2-r1; PostgreSQL añade nghttp2-libs 1.70.0-r0. Versiones exactas mediante
  los repositorios firmados de Alpine. Si dejan de estar disponibles, el build
  falla y exige revisar los pins.
- Producción exige referencias explícitas de las cuatro imágenes validadas.
  No usa los tags de desarrollo por defecto. Worker y migraciones comparten
  la imagen backend mediante el mismo parámetro.

La excepción dev-only SEC-2026-001 sigue siendo independiente y temporal.
Los resultados finales de esta remediación deben constar en el registro de
entrega con comandos y logs reales; este documento describe el control.

Fuentes primarias consultadas: [release oficial Trivy 0.76.0](https://github.com/aquasecurity/trivy/releases/tag/v0.76.0),
[entrada de archivo y opciones de escaneo](https://trivy.dev/docs/latest/references/configuration/cli/trivy_image/),
[conversión CycloneDX](https://trivy.dev/docs/latest/references/configuration/cli/trivy_convert/),
[Dockerfile PostgreSQL Alpine oficial](https://github.com/docker-library/postgres/blob/master/18/alpine3.24/Dockerfile)
y [entrypoint oficial](https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh).
Se revisó también el [incidente de Trivy de marzo de 2026](https://github.com/aquasecurity/trivy/discussions/10425);
no usar las versiones comprometidas ni etiquetas mutables del escáner.
