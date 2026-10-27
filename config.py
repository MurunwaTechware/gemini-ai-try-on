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
MAX_RESULT_BYTES = 20 * 1024 * 1024      # a generated result being saved: never sent to Gemini, can be bigger
# Inline requests cap out around 20 MB and base64 adds ~33%, so keep the raw total under ~14 MB
MAX_TOTAL_BYTES = 14 * 1024 * 1024
MAX_OUTPUTS = 4                          # the repo fired 10; 4 is plenty for a demo
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
GARMENT_SLOTS = ("top", "bottom", "headwear", "eyewear")   # order they are sent to the model

# Where uploads and results are kept (storage.py):
#   "local": a folder, RUNS_DIR. With a queue, the web server and workers must share it (Docker Compose mounts it)
#   "gcs":   the Cloud Storage bucket RESULTS_BUCKET, so the web server and workers can run on different machines
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
if STORAGE_BACKEND not in ("local", "gcs"):
    raise RuntimeError(f"STORAGE_BACKEND must be 'local' or 'gcs', not {STORAGE_BACKEND!r}.")
RUNS_DIR = Path(os.getenv("RUNS_DIR", Path(__file__).parent / "results"))
RESULTS_BUCKET = os.getenv("RESULTS_BUCKET")   # Terraform output results_bucket
if STORAGE_BACKEND == "gcs" and not RESULTS_BUCKET:
    raise RuntimeError("STORAGE_BACKEND=gcs needs RESULTS_BUCKET (the bucket's name) in .env.")

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

# Sign-in on or off. Off (AUTH_ENABLED=false in .env) is for local development and demos: there's no
# Google sign-in, user records stay in memory, and every request is the demo user, whose
# files go in results/<DEMO_USER_ID>/. On unless .env says otherwise, so a missing setting can't
# quietly switch sign-in off on a real server.
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
DEMO_USER_ID = "demo-user"   # the same ID as the frontend's mock user

# Sign-in (Firebase Authentication, "Continue with Google"). The web config values below are public
# by design: they only identify the Firebase project to the browser and grant no access by themselves.
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", PROJECT_ID)
FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", f"{FIREBASE_PROJECT_ID}.firebaseapp.com")

# Job status records in Firestore (job_status.py): how the upload page hears that its measurement has
# moved on or finished, from whichever worker ran it. Used with sign-in on or off. If Firestore can't be
# reached, the page still finds the result by checking the job folder, a few seconds later.
JOB_STATUS_ENABLED = os.getenv("JOB_STATUS_ENABLED", "true").lower() != "false" and bool(FIREBASE_PROJECT_ID)
# How long a sign-in lasts. Firebase allows 5 minutes to 14 days.
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "5"))
# Session cookie is HTTPS-only. Chrome, Edge and Firefox treat http://localhost as secure, so this can
# stay on locally; set to false only for a browser that doesn't (e.g. Safari) or a plain-HTTP host.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"
