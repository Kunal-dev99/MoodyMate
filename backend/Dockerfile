# syntax=docker/dockerfile:1
# MoodyMate backend image (BE-34.2, D-62 §5). Build context: backend/.
#
#   docker build -t moodymate-backend backend/
#
# * Multi-stage: dependencies come from uv.lock only. `--locked` (not `--frozen`, which
#   installs a stale lock silently) fails the build on a pyproject/lock mismatch. No dev
#   extra, no build tools in the runtime stage.
# * Non-root: the runtime runs as uid/gid 10001 and owns nothing but the artifact volume.
# * No secret is baked in: settings (DATABASE_URL, OIDC_*, METRICS_TOKEN, ...) come from the
#   environment or a secret store at run time (D-50 §2). `.dockerignore` keeps `.env` out.
# * `--proxy-headers`: X-Forwarded-For/-Proto are trusted only from FORWARDED_ALLOW_IPS
#   (read by uvicorn from the environment; the default trusts only localhost).
# * Migrations are not run by this command: the compose `prod` profile runs a one-shot
#   `alembic upgrade head` with the same image first.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
# The locked runtime dependencies only (the project itself is copied as source below), with
# the `s3` extra so the image can serve ARTIFACT_STORE_KIND=s3 (D-59 §6, BE-33.6).
RUN uv sync --locked --no-dev --extra s3 --no-install-project

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 moodymate \
    && useradd --system --uid 10001 --gid moodymate --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin moodymate \
    && install -d -o 10001 -g 10001 -m 0750 /var/lib/moodymate/artifacts
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# Source and migrations, owned by root and read-only to the app user.
COPY alembic.ini ./
COPY app ./app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARTIFACT_STORE_DIR=/var/lib/moodymate/artifacts \
    FORWARDED_ALLOW_IPS=127.0.0.1
VOLUME ["/var/lib/moodymate/artifacts"]
USER 10001:10001
EXPOSE 8000
# Liveness only (no I/O); orchestrators gate traffic on /api/v1/health/ready.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2).status != 200)"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
