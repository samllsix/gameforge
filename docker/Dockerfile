FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r gameforge \
    && useradd -r -g gameforge -d /app -s /usr/sbin/nologin gameforge

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ config/
COPY src/ src/
COPY static/ static/
COPY pyproject.toml README.md ./

RUN mkdir -p data logs logs/security \
    && chown -R gameforge:gameforge /app

USER gameforge

ENV GAMEFORGE_HOST=0.0.0.0 \
    GAMEFORGE_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${GAMEFORGE_PORT}/health || exit 1

CMD ["python", "-m", "src.api.main"]
