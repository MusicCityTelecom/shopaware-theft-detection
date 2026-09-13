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
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend.py ./
COPY shopaware ./shopaware
RUN mkdir -p /app/alerts /app/incidents /app/models /app/data

ENV SHOPAWARE_DB_PATH=/app/data/shopaware.db \
    SHOPAWARE_ALERT_DIR=/app/alerts \
    SHOPAWARE_INCIDENT_DIR=/app/incidents \
    SHOPAWARE_KEY_FILE=/app/data/.shopaware.key

EXPOSE 8000
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
