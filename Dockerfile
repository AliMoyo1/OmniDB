# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Non-root runtime user. Pre-create and own the quarantine mount point: it lives under
# /var/lib (root-owned by default) and both web and worker mount the same named volume
# there, so ownership must be set here for Docker to carry it into the fresh volume.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app && \
    mkdir -p /var/lib/ciphercontact/quarantine && \
    chown -R app:app /var/lib/ciphercontact

WORKDIR /app

# Install the application and its dependencies.
# TODO: switch to installing from the hash-pinned lockfile once generated (scripts/lock.sh).
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
