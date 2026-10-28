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
#   docker run --rm -p 8000:8000 --env-file .env -e GOOGLE_APPLICATION_CREDENTIALS=/gcloud/adc.json `
#     -v "$env:APPDATA\gcloud\application_default_credentials.json:/gcloud/adc.json:ro" `
#     -v "${PWD}\results:/app/results" `
#     tryon
# Then open http://localhost:8000
#
# Queue mode (RabbitMQ + web server + worker): use compose.yaml instead:
#   docker compose up --build

FROM python:3.14-slim

# No .pyc files, and logs appear immediately instead of being buffered.
# No GOOGLE_APPLICATION_CREDENTIALS here: on Cloud Run, credentials come from the service's own service
# account, and pointing at a login file that isn't there would break every Google call. Locally, Compose
# (or `docker run -e`) sets it to the mounted login file.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall them on every build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY models ./models
COPY static ./static

# Run as a normal user, not root. UID 1000 matches the usual first user on Linux hosts,
# so results written to the mounted folder stay editable there.
RUN useradd --uid 1000 --create-home app && mkdir -p results && chown app:app results
USER app

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
