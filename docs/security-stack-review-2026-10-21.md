# Revisión de tecnologías — 21 de octubre de 2026

Alcance: repositorio, versiones y servicios locales, locks Python/npm e imágenes.
No se inspeccionó Contabo, configuración de su host, Resend o la implementación
interna del Object Storage. Ausencia de hallazgos conocidos no demuestra ausencia
de vulnerabilidades. Las versiones se contrastan con fuentes primarias y escáneres;
una versión nueva por sí sola no constituye una corrección de seguridad necesaria.

| Tecnología | Versión observada | Resultado / acción |
| --- | --- | --- |
| Node frontend | 24.22.0 | Actualización de imagen y herramientas a 24.22.1, con digest oficial fijado |
| Python | 3.13.16 | Imágenes backend y auditoría actualizadas a 3.13.17; parche de pip/certificados |
| Next.js | 16.4.2 | Ya supera 16.4.1 corregida en aviso del 14 de octubre; latest npm consultado 16.4.2 |
| React / React DOM | 19.3.1 | Conservadas; revisión por npm audit y escaneo runtime |
| FastAPI / Starlette | 0.143.3 / 1.8.0 | Incluidas en auditoría de todos los locks Python |
| Pydantic / core | 2.14.1 / 2.50.1 | Incluidas en auditoría Python |
| SQLAlchemy / Alembic | 2.0.54 / 1.20.0 | Incluidas en auditoría Python; sin migración a SQLAlchemy 2.1 |
| Uvicorn / Psycopg | 0.54.0 / 3.3.6 | Incluidas en auditoría Python |
| Pillow / cryptography | 13.0.0 / 50.0.2 | Incluidas en auditoría Python |
| boto3 / botocore | 1.43.114 / 1.43.114 | Incluidas en auditoría Python |
| PostgreSQL | 18.6 | Menor vigente de la rama 18 según política oficial |
| Nginx | 1.30.6 | Aviso CVE-2026-15176 declara 1.30.6+ corregida |
| Alpine musl / musl-utils | 1.2.6-r2 en PostgreSQL/proxy | CVE-2026-107891; se fija parche 1.2.6-r3 y se reconstruyen ambas imágenes |
| pip herramientas backend | 26.2.1 en lock de desarrollo | Se fija 26.2.2 con hashes oficiales de PyPI |
| Docker Engine/CLI local | 29.9.1 | Pendiente mantenimiento del host a 29.9.2; corrección de certificados Go CVE-2026-111943 |
| Docker Compose local | 5.6.0 | Pendiente mantenimiento del host a 5.6.1 por la misma corrección de certificados |

Docker/Compose del host no se modificaron durante las pruebas del proyecto.
La revisión de versiones de host deberá repetirse en la VPS antes de desplegar.
El runtime backend elimina pip y el frontend elimina npm; aun así se parchean las
imágenes de construcción y las herramientas, que también necesitan TLS confiable.
No se modificó el runtime Node/Python que pertenece a la aplicación Codex.

## Hallazgo npm preexistente

El registro oficial consultado aún ofrece `@next/eslint-plugin-next` 16.4.2,
`braces` 3.0.3 y `micromatch` 4.0.8. Existe `fast-glob` 3.3.3, pero actualizarlo
solo no elimina la cadena completa denunciada por el escáner. Se conserva la
excepción existente SEC-2026-001, cuatro hallazgos de desarrollo, con vencimiento
31 de octubre. No se desactivan reglas ni se aplica el downgrade mayor sugerido
por npm. La URL de advisory de esa excepción devolvió 404 en la consulta web;
se conserva como limitación de trazabilidad, no como prueba de que el hallazgo
haya desaparecido. La salida real de npm audit permanece en evidencia privada.

## Fuentes primarias consultadas

- [Node 24.22.1, seguridad](https://nodejs.org/en/blog/release/v24.22.1).
- [Aviso Node de octubre](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases).
- [Python 3.13.17](https://www.python.org/downloads/release/python-31317/).
- [Next.js, correcciones del 14 de octubre](https://nextjs.org/blog/nextjs-security-update-october-14-2026).
- [PostgreSQL, versiones soportadas](https://www.postgresql.org/support/versioning/).
- [Nginx, avisos](https://nginx.org/en/security_advisories.html).
- [Docker 29.9.2](https://docs.docker.com/engine/release-notes/29/).
- [Compose 5.6.1](https://github.com/docker/compose/releases/tag/v5.6.1).
- Advisories oficiales de FastAPI, Pydantic, SQLAlchemy, Alembic y Uvicorn en sus repositorios GitHub.
- Registro oficial npm: versiones de Next, React y cadena ESLint. Sin ejecutar paquetes descargados para consultar metadatos.

El escaneo inicial de PostgreSQL y Nginx encontró dos paquetes musl por imagen,
severidad todavía UNKNOWN, con corrección 1.2.6-r3. La clasificación no se usó
para omitir un parche disponible. Backend y frontend reconstruidos produjeron
cero hallazgos conocidos en el escaneo runtime. La base de Trivy se comprobó
vigente y se guardó su hash y los identificadores inmutables de imagen junto al SBOM.

Pip 26.2.2 se contrastó con metadatos oficiales de
[PyPI](https://pypi.org/project/pip/26.2.2/); el lock anterior anulaba el parche
de pip de la nueva imagen de desarrollo. Actualizar solo el FROM no lo resolvía.

Reescaneo posterior al parche de musl: cero hallazgos conocidos en PostgreSQL
y Nginx. Los cuatro runtime finales escaneados quedan sin hallazgos conocidos
con esa base vigente. Esto no cubre el motor Docker del host, cuyo parche sigue
pendiente, ni elimina la excepción npm de desarrollo. Se confirmó en ejecución
Node 24.22.1 y Python 3.13.17; las 15 pruebas nuevas también se repitieron con
pip 26.2.2 instalado mediante hashes y PostgreSQL con musl corregido.

Comandos reproducibles y resultados finales se registran en la entrega 3.45.
