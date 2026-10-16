# PatientZero: one service = FastAPI API + static Next.js UI on the same origin.
# FalkorDB runs as a SEPARATE service (falkordb/falkordb-server image with a volume on /data). See DEPLOY.md.

# ---- 1. static frontend -------------------------------------------------------------------------------
FROM node:20-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# "." makes the UI call ./api/... on its own origin. Override with an absolute URL only for a split deploy.
ARG NEXT_PUBLIC_API_URL=.
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_OUTPUT=export NEXT_TELEMETRY_DISABLED=1
RUN npm run build && test -f out/index.html

# ---- 2. API -------------------------------------------------------------------------------------------
FROM python:3.13-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN pip install uv==0.9.* || pip install uv
COPY backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project
COPY backend/ backend/
COPY queries/ queries/
COPY ingest/ ingest/
COPY data/ data/
COPY scripts/start-prod.sh scripts/start-prod.sh
COPY --from=web /web/out /app/web
ENV FRONTEND_DIR=/app/web PORT=8000 SEED_ON_START=true
RUN useradd -m -u 10001 app && chown -R app /app/data
USER app
EXPOSE 8000
CMD ["sh", "scripts/start-prod.sh"]
