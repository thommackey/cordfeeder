FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home app

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY cordfeeder/ cordfeeder/

# Install the project itself
RUN uv sync --frozen --no-dev

USER app

CMD ["/app/.venv/bin/python", "-m", "cordfeeder"]
