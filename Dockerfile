FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY swegon_mcp/ swegon_mcp/

# Install dependencies
RUN uv pip install --system -e .

# Config is mounted at runtime
VOLUME ["/config"]

EXPOSE 8000

ENV SWEGON_API_KEY=""

CMD ["swegon-mcp", "--http", "/config/config.yaml"]
