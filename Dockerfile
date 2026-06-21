# syntax=docker/dockerfile:1

# ---- Stage 1: build the React/Vite frontend --------------------------------- #
FROM node:22-slim AS frontend
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---- Stage 2: FastAPI backend serving the built frontend -------------------- #
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

# tzdata so the container's local date matches the household's timezone, not
# UTC. `current_week()` uses date.today(); override TZ via a Railway variable.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TZ=America/New_York

# Install dependencies from the lockfile first so they stay cached across
# source changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8000
EXPOSE 8000

# Railway injects $PORT; default to 8000 for local `docker run`.
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}"]
