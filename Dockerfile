FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    TIKTOKEN_CACHE_DIR="/opt/tiktoken" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY agent-data ./agent-data
COPY kb ./kb

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
