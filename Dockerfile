FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      libgl1 \
      libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install torch torchvision --index-url ${TORCH_INDEX_URL} && pip install -r requirements.txt

COPY backend.py VERSION ./
COPY shopaware ./shopaware
COPY tools ./tools
RUN mkdir -p /app/alerts /app/incidents /app/models /app/data

ENV SHOPAWARE_DB_PATH=/app/data/shopaware.db \
    SHOPAWARE_ALERT_DIR=/app/alerts \
    SHOPAWARE_INCIDENT_DIR=/app/incidents \
    SHOPAWARE_KEY_FILE=/app/data/.shopaware.key \
    SHOPAWARE_MODEL_DIR=/app/models \
    SHOPAWARE_MEDIA_WRITER=ffmpeg

ENV YOLO_CONFIG_DIR=/app/data/ultralytics
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4)"

EXPOSE 8000
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
