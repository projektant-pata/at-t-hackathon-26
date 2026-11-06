# One image, three entrypoints: ETL (one-shot), REST API, MCP server.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app
# dependency layer (cached unless pyproject/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --group dev

COPY . .
ARG UID=1000
ARG GID=1000
RUN groupadd --gid ${GID} app && useradd --create-home --uid ${UID} --gid ${GID} app \
 && mkdir -p /app/out /app/.cache \
 && chown -R app:app /app/out /app/.cache
USER app

ENV REPO_ROOT=/corpus \
    CS_OUT_DIR=/app/out \
    CS_CACHE_DIR=/app/.cache \
    CS_GRAPH_BACKEND=neo4j

CMD ["python", "-m", "etl_pipeline.run_etl"]
