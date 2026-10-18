FROM postgres:18-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873
# Start directly as PostgreSQL's existing UID. No root-to-user helper is needed.
# The official image owns PGDATA's parent and the socket directory as postgres.
RUN apk add --no-cache 'zlib=1.3.2-r1' 'nghttp2-libs=1.70.0-r0' \
    && rm /usr/local/bin/gosu
USER 70:70
