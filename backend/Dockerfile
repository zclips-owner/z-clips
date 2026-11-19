# Z CLIPS backend — FastAPI + worker in one container (Render free tier has no separate workers)
FROM python:3.12-slim

# ffmpeg is required for the clip pipeline (scene detection, rendering, thumbnails)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Storage for uploads / rendered clips (ephemeral on Render free tier)
RUN mkdir -p storage storage/models

EXPOSE 8000
CMD ["bash", "start.sh"]
