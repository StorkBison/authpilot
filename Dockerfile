# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first for layer caching
COPY pyproject.toml README.md ./
COPY authpilot/__init__.py authpilot/__init__.py
RUN pip install .

COPY authpilot ./authpilot
COPY data ./data
COPY scripts ./scripts
RUN pip install --no-deps . \
    && useradd --create-home --uid 1000 app && mkdir -p /app/var && chown -R app /app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "authpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
