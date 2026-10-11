# Python runs in Docker; Ollama runs natively on the host (GPU access).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install dependencies only (source is bind-mounted at runtime for fast iteration).
COPY pyproject.toml .
RUN pip install --no-cache-dir uv \
 && uv pip install --system --no-cache -r pyproject.toml --extra dev

CMD ["pytest"]
