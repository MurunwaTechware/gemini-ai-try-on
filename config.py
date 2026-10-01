"""Environment-driven settings. Nothing in here talks to Google."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Default is Vertex AI via Application Default Credentials (gcloud auth application-default login).
# Setting GEMINI_API_KEY switches to the Gemini Developer API instead.
API_KEY = os.getenv("GEMINI_API_KEY")    # from .env, never from a committed file
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
if not API_KEY and not PROJECT_ID:
    raise RuntimeError("Set GOOGLE_CLOUD_PROJECT (Vertex AI + ADC) or GEMINI_API_KEY in .env.")
# Nano Banana 2. The repo's gemini-2.0-flash-exp-image-generation is retired,
# and gemini-2.5-flash-image shuts down Oct 2, 2026.
MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
# Reads the three body photos and returns measurements as JSON; no image output needed
# gemini-3-pro is listed on Vertex but this project gets 404 when generating with it
MEASURE_MODEL = os.getenv("GEMINI_MEASURE_MODEL", "gemini-2.5-pro")

MAX_IMAGE_BYTES = 7 * 1024 * 1024        # inline-data limit per image
# Inline requests cap out around 20 MB and base64 adds ~33%, so keep the raw total under ~14 MB
MAX_TOTAL_BYTES = 14 * 1024 * 1024
MAX_OUTPUTS = 4                          # the repo fired 10; 4 is plenty for a demo
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
GARMENT_SLOTS = ("top", "bottom", "headwear", "eyewear")   # order they are sent to the model

RUNS_DIR = Path(os.getenv("RUNS_DIR", Path(__file__).parent / "results"))

# Where measurement jobs run:
#   "inprocess": a background task inside the web server; nothing else to run
#   "rabbitmq":  the web server publishes jobs to RabbitMQ and `python worker.py` runs them
QUEUE_BACKEND = os.getenv("QUEUE_BACKEND", "inprocess")
if QUEUE_BACKEND not in ("inprocess", "rabbitmq"):
    raise RuntimeError(f"QUEUE_BACKEND must be 'inprocess' or 'rabbitmq', not {QUEUE_BACKEND!r}.")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
# Jobs one worker runs at once. Each job is ~30 s of waiting on Gemini, so this can be above 1;
# keep (workers x this) within the project's Gemini quota or the extra calls fail with 429s.
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "4"))
