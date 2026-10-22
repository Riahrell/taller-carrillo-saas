FROM nginx:stable-alpine@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5
# CVE-2026-77214: retain the distribution-signed fixed package explicitly.
# Updated scanner evidence also requires the distribution fixes for libX11/libXpm.
# CVE-2026-107891: use Alpine's patched musl revision.
RUN apk add --no-cache 'musl=1.2.6-r3' 'musl-utils=1.2.6-r3' \
    'libexpat=2.9.0-r0' 'zlib=1.3.2-r1' \
    'libx11=1.8.14-r0' 'libxpm=3.5.20-r0'
USER 101:101
