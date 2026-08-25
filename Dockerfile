# Jiro Search API — container image (PRD §6.6.4)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JIRO_CONFIG=/etc/jiro/config.yaml

WORKDIR /app

# Install dependencies first for better layer caching
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install .

# Default config (override with env vars or mount your own at /etc/jiro/config.yaml)
RUN mkdir -p /etc/jiro /data/jiro && \
    python -c "from jiro.config import DEFAULT_CONFIG; import yaml; open('/etc/jiro/config.yaml','w').write(yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False))"

ENV JIRO_DB__PATH=/data/jiro/jiro.db \
    JIRO_CACHE__PATH=/data/jiro/cache.db \
    JIRO_SERVER__HOST=0.0.0.0

VOLUME ["/data/jiro"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

CMD ["jiro", "serve", "--host", "0.0.0.0"]
