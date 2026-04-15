FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /usr/local/bin/

COPY requirements.lock .
RUN uv pip install --system --no-cache --require-hashes --requirement requirements.lock

COPY src/ src/
COPY tests/ tests/

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
