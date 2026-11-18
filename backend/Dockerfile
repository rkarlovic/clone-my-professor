# CMP backend image: the API (uvicorn), the worker (Procrastinate) and the migrations share it,
# with different commands (infra/docker-compose.yml). Multi-stage, pinned bases, non-root.
# Model weights and course data are never baked in: they live in mounted volumes.

FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

FROM python:3.12.15-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first (cached layer): the ingest group brings Docling (CPU PyTorch on Linux).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group ingest --no-install-project
COPY . .
RUN uv sync --frozen --no-dev --group ingest

FROM python:3.12.15-slim-bookworm
# Docling's PDF backends need these shared libraries at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 cmp \
    && mkdir -p /data /config \
    && chown cmp:cmp /data
WORKDIR /app
COPY --from=build --chown=cmp:cmp /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hf \
    UPLOAD_DIR=/data/uploads \
    LTI_TOOL_KEY_FILE=/data/keys/lti_tool.pem \
    CMP_MODELS_CONFIG=/config/models.yaml
USER cmp
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
