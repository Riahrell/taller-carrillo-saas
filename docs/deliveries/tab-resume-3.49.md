# 3.49 — Conservar la pantalla al regresar a la pestaña

Se conserva el contenido visible durante la comprobación de sesión en catálogo,
inventario, consulta de existencias, personal y panel de acceso. El indicador de
revalidación usa `sr-only` para no desplazar el formulario ni la página. Las
acciones permanecen suspendidas mediante `inert` hasta confirmar el acceso; un
fallo o cambio de contexto sigue retirando los datos. Se conserva la carga inicial
visible. Las miniaturas cargadas no se vuelven a descargar por foco, pageshow o
visibilitychange; el cambio explícito de foto y Reintentar sí permiten renovarlas.

Archivos: los seis componentes correspondientes y tres archivos de pruebas en
frontend. No hay cambios de esquema, dependencias ni permisos del servidor.

Validación ejecutada en frontend:

```sh
npm test -- --reporter=dot
npm run lint
npm run typecheck
```

Resultados: 344 pruebas aprobadas (28 archivos), ESLint y TypeScript sin errores.
Las pruebas conservan el borrador y verifican su retirada al cambiar la empresa;
comprueban además que recuperar el foco reutiliza el blob y cambiar la foto lo
renueva. No se realizó una prueba manual de desplazamiento en navegador.

Prueba manual pendiente: abrir un producto, desplazarse, completar un borrador y
cambiar de pestaña y ventana varias veces. Verificar que permanecen texto, foto y
posición; comprobar después el cierre de sesión desde otra pestaña. Las
operaciones de escritura con resultado incierto conservan su tratamiento de
seguridad existente. La consulta operativa sigue consultando existencias y permiso
vigente al volver; no se elimina esa comprobación de autorización.

Siguiente tarea: revisión manual del inventario antes de preparar el despliegue.
