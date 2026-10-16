FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/backend
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && useradd --system --uid 10001 --create-home app
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini pyproject.toml ./
USER 10001
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS tests
USER root
COPY requirements-dev.lock ./requirements-dev.lock
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY tests ./tests
USER 10001
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests"]
