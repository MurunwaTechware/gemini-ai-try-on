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

# System libraries for the pose check (MediaPipe). MediaPipe sets up an OpenGL ES
# context even when it runs on the CPU, so it needs the EGL/GLES libraries, and
# OpenCV needs libxcb — a -slim image has none of them. Without these the pose
# check fails to load and, because it is built to fail open, every photo would
# sail through unchecked. libgomp1 is OpenMP, which TensorFlow Lite uses.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libegl1 libgles2 libgl1 libglib2.0-0 libgomp1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so code changes don't reinstall them on every build.
# MediaPipe depends on opencv-contrib-python, the desktop build, which wants the
# whole X11 stack. We swap it for the headless build afterwards: same cv2 module,
# no X11, a much smaller image. MediaPipe only ever does `import cv2`, so it
# cannot tell the difference.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y opencv-contrib-python opencv-python \
 && pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless

COPY *.py ./
COPY models ./models
COPY static ./static

# Bake the pose model into the image rather than downloading it on first use —
# no network call on the first scan after a deploy, and no failure if the
# container cannot reach Google.
ENV POSE_MODEL_PATH=/app/models/pose_landmarker_lite.task

# Run as a normal user, not root. UID 1000 matches the usual first user on Linux hosts,
# so results written to the mounted folder stay editable there.
RUN useradd --uid 1000 --create-home app && mkdir -p results && chown app:app results
USER app

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
