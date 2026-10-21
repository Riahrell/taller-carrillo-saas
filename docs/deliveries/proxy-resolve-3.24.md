# Recuperación del acceso local tras recrear contenedores

Causa observada: Nginx seguía conectando a la dirección interna anterior del
backend tras actualizarlo. Backend saludable y proxy unhealthy; health daba 502
y /auth/session era convertido por el proxy en 503, mostrando indisponibilidad.

Se reinició el proxy para recuperar el servicio. Corrección persistente:
resolver interno Docker 127.0.0.11, TTL cinco segundos, timeout tres segundos y
upstreams con zone/resolve para backend y frontend. Conserva las rutas,
reescrituras, cabeceras, cuotas, CSRF y red privada existentes. El generador TLS
usa el mismo upstream en su health interno; no se desplegó nada en la VPS.

Archivos completos: ops/proxy/nginx.conf, scripts/render_tls_proxy.py, este registro.

Comandos operativos ejecutados:
```bash
docker compose --project-name carrillo-dev --env-file .local/development/compose.env -f compose.yaml -f compose.dev.yaml -f compose.images.yaml -f compose.identity.yaml restart proxy
docker exec carrillo-dev-proxy-1 nginx -t
docker exec carrillo-dev-proxy-1 nginx -s reload
git diff --check
```

Nginx 1.30.6: sintaxis correcta y recarga aceptada. Comprobación HTTP sin cookies
ni credenciales: GET http://localhost:8080/api/health/live → 200;
GET /api/auth/session con X-Requested-With: XMLHttpRequest y Origin local → 401
esperado, sin sesión, en lugar de 503. No se inició sesión ni se enviaron correos.
No se ejecutó batería funcional, ni se simuló otra sustitución de contenedores.
La recuperación automática ante la siguiente recreación queda pendiente de
observación; un arranque puede tener un intervalo breve de resolución DNS.

Referencia: https://nginx.org/en/docs/http/ngx_http_upstream_module.html#resolve

Prueba del propietario: recargar localhost:8080 o pulsar «Comprobar acceso».
Debe permitir iniciar el acceso normal. El usuario verifica contraseña y correo.
Siguiente tarea: atender ese resultado antes de continuar con otros módulos.
