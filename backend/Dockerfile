# GT Pulse backend — production container (Railway / any Docker host).
# Uses the official uv + Python 3.12 base so the build matches local (uv.lock frozen).
# NO secrets are baked in — every credential is supplied at runtime as a Railway env var
# (see .dockerignore: .env* is never copied into the image).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 1) deps first (cached layer) — install from the frozen lock without the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) app source + params, then finish the sync (installs the project itself).
COPY . .
RUN uv sync --frozen --no-dev

# Railway injects $PORT; bind to it (fall back to 8000 for local `docker run`).
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
