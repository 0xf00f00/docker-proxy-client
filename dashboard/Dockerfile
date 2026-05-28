# syntax=docker/dockerfile:1.9

FROM node:24-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

FROM python:3.12-slim-bookworm AS runtime
COPY --from=docker:29.5-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:29.5-cli /usr/local/libexec/docker/cli-plugins/docker-compose \
                            /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /app/.venv /app/.venv
COPY backend/ /app/
COPY --from=frontend /fe/dist /app/static

EXPOSE 8080
CMD ["python", "-m", "app.main"]
