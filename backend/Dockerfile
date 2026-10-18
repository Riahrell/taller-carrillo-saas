FROM python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a AS base
RUN apk add --no-cache 'zlib=1.3.2-r1'

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/backend
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && addgroup -S -g 10001 app \
    && adduser -S -D -u 10001 -G app app
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini pyproject.toml ./
USER 10001
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "64"]

FROM base AS tests
USER root
COPY requirements-dev.lock ./requirements-dev.lock
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY tests ./tests
USER 10001
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests"]

# Package installation tools are unnecessary in the deployed runtime.
FROM base AS runtime
USER root
RUN python -m pip uninstall -y pip
USER 10001
