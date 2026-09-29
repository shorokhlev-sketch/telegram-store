# syntax=docker/dockerfile:1
#
# Production image for apple-store-tg: builds the web SPA, then packages the
# FastAPI/aiogram backend that serves both the API and the built SPA (see
# app/main.py's SPA fallback). Runs behind Caddy (deploy/docker-compose.yml) -
# this container is never reached directly from the internet.

# ---------------------------------------------------------------------------
# Stage 1: build the SPA. VITE_BASE=/ because in production the app owns the
# whole domain (lab uses a sub-path instead, see deploy/lab-deploy.sh).
# ---------------------------------------------------------------------------
FROM node:20-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
ENV VITE_BASE=/
RUN npm run build
# sanity check, same as deploy/lab-deploy.sh does after its build
RUN test -f dist/index.html

# ---------------------------------------------------------------------------
# Stage 2: uv, to install backend dependencies from the locked pyproject/uv.lock
# without needing network access at container-build time beyond the lockfile.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.5 AS uv

# ---------------------------------------------------------------------------
# Stage 3: runtime image.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Repo layout is mirrored 1:1 (backend/, web/dist) so Settings.web_dist_dir's
# default - <repo root>/web/dist, computed from backend/app/config.py - keeps
# working unchanged; WEB_DIST_DIR is not overridden.
WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/

# Dependencies first (better layer caching): only pyproject.toml/uv.lock
# invalidate this layer, not app code changes.
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project

COPY backend/app ./backend/app
COPY backend/alembic ./backend/alembic
COPY backend/alembic.ini ./backend/alembic.ini
RUN cd backend && uv sync --frozen --no-dev

COPY --from=web-build /web/dist ./web/dist

# Non-root user. /data is where the compose volume (appdata) is mounted for
# the sqlite db and media; owning it here means a fresh, empty named volume
# inherits this ownership on first mount (Docker copies the image's directory
# tree into an empty named volume), so the app can write to it without root.
RUN groupadd -r app && useradd -r -g app -d /app app \
    && mkdir -p /data/media \
    && chown -R app:app /app /data
USER app

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/app.db \
    MEDIA_DIR=/data/media

WORKDIR /app/backend
EXPOSE 8000

# Migrate then serve. `--proxy-headers --forwarded-allow-ips='*'` makes
# uvicorn trust X-Forwarded-For/X-Forwarded-Proto from whoever connects to
# it directly. That's safe here specifically because this container is
# reachable ONLY from the Caddy container over the private compose network
# (docker-compose.yml publishes no ports for `app`) - the immediate peer is
# always our own trusted reverse proxy, never an arbitrary client. This
# matters beyond correctness: app/api/leads.py's rate limiting keys off
# request.client.host, which uvicorn's proxy-headers middleware sets from
# that forwarded header - get this wrong and every lead looks like it came
# from the Caddy container's IP, and the per-IP rate limit becomes useless.
CMD ["sh", "-c", "uv run --no-dev alembic upgrade head && exec uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
