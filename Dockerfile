# Gemini virtual try-on: web server (default) or queue worker, from one image.
#
# One-time setup on the host:
#   1. Log in to Google Cloud:   gcloud auth application-default login
#   2. Create your settings:     copy .env.example to .env and set GOOGLE_CLOUD_PROJECT
#
# Build:
#   docker build -t tryon .
#
# Run (Windows PowerShell; on macOS/Linux use ~/.config/gcloud/application_default_credentials.json
# for the credentials path and $(pwd) instead of ${PWD}):
#   docker run --rm -p 8000:8000 --env-file .env `
#     -v "$env:APPDATA\gcloud\application_default_credentials.json:/gcloud/adc.json:ro" `
#     -v "${PWD}\results:/app/results" `
#     tryon
# Then open http://localhost:8000
#
# Queue mode (RabbitMQ + web server + worker): use compose.yaml instead:
#   docker compose up --build

FROM python:3.14-slim

# No .pyc files, and logs appear immediately instead of being buffered
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GOOGLE_APPLICATION_CREDENTIALS=/gcloud/adc.json

WORKDIR /app

# Dependencies first, so code changes don't reinstall them on every build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static ./static

# Run as a normal user, not root. UID 1000 matches the usual first user on Linux hosts,
# so results written to the mounted folder stay editable there.
RUN useradd --uid 1000 --create-home app && mkdir -p results && chown app:app results
USER app

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
