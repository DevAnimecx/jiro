FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY jiro/ ./jiro/
COPY scripts/ ./scripts/

# Create data directory
RUN mkdir -p /data

# Set environment variables
ENV JIRO_DB_PATH=/data/jiro.db
ENV JIRO_HOST=0.0.0.0
ENV JIRO_PORT=8000

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/v1/monitor/health')" || exit 1

CMD ["python", "-m", "uvicorn", "jiro.server:create_app", "--host", "0.0.0.0", "--port", "8000"]