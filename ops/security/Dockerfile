FROM python:3.13.17-slim@sha256:19ed48e5e9c132082510ba9efb11bc2098d582f610374670fa30b6b17026617b
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp
COPY requirements.lock /tools/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tools/requirements.lock
USER 10001:10001
ENTRYPOINT ["python", "-m", "pip_audit"]
