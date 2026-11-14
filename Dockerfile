# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV OTEL_SDK_DISABLED=true \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so application edits do not invalidate the wheel layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && opentelemetry-bootstrap -a install

COPY app ./app
COPY seed ./seed

# Run unprivileged; the image carries no writable state.
RUN useradd --system --uid 10001 --create-home tracker \
    && chown -R tracker:tracker /app
USER tracker

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2).status==200 else 1)"

# opentelemetry-instrument is always wired; OTEL_SDK_DISABLED decides whether it
# does anything, so tracing is a one-line config change with no rebuild.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
