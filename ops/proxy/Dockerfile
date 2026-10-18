FROM nginx:stable-alpine@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5
# CVE-2026-77214: retain the distribution-signed fixed package explicitly.
RUN apk add --no-cache 'libexpat=2.9.0-r0' 'zlib=1.3.2-r1'
USER 101:101
