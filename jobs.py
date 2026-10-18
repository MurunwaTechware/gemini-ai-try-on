"""Measurement jobs: everything about a job that doesn't depend on how it was delivered.
Used by the web server (in-process mode) and by worker.py (queue mode), so no FastAPI in here.

A job is a measurement profile: a folder RUNS_DIR/<user ID>/measurement_profiles/<job ID> holding the photos
and inputs.json. Try-ons the user saves are kept apart, in RUNS_DIR/<user ID>/saved_tryons/<ID>. Queue messages
carry only the two IDs; the worker reads everything else from the folder. With workers on other machines,
RUNS_DIR has to be shared storage (e.g. a Cloud Storage bucket) rather than a local folder.
"""
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path

import config
import gemini_client
import prompts

INPUTS_FILE = "inputs.json"
MEASUREMENT_FILE = "measurement.json"
ERROR_FILE = "error.json"
PROFILES_DIR = "measurement_profiles"   # inside each user's folder
TRYONS_DIR = "saved_tryons"             # only try-ons the user chose to save; generating alone keeps nothing
TRYON_META_FILE = "meta.json"           # in each saved try-on: the profile it came from, and the model

JOB_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")   # 20260930-123847-f5e5cc
USER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")    # Firebase UIDs from Google/Apple sign-in are alphanumeric


def user_dir(user_id: str) -> Path:
    """Everything one user has uploaded or generated. Checked, so an ID can't climb out with "../"."""
    if not USER_ID.fullmatch(user_id):
        raise ValueError(f"Invalid user ID: {user_id!r}")
    return config.RUNS_DIR / user_id


def job_path(user_id: str, job_id: str) -> Path:
    """The job's folder, RUNS_DIR/<user ID>/measurement_profiles/<job ID>. Doesn't check that it exists."""
    if not JOB_ID.fullmatch(job_id):
        raise ValueError(f"Invalid job ID: {job_id!r}")
    return user_dir(user_id) / PROFILES_DIR / job_id


def is_finished(job_dir: Path) -> bool:
    return (job_dir / MEASUREMENT_FILE).exists() or (job_dir / ERROR_FILE).exists()


def _load_photos(job_dir: Path) -> dict[str, gemini_client.InputImage]:
    photos = {}
    for view in prompts.MEASURE_VIEWS:
        path = next(job_dir.glob(f"{view}.*"))   # saved as front.jpg, left.png, ...
        photos[view] = gemini_client.InputImage(path.read_bytes(), mimetypes.guess_type(path.name)[0])
    return photos


async def run_measurement(job_dir: Path) -> None:
    """Measure one job and write measurement.json, or error.json on any failure. Never raises
    for model problems, so a queued message is handled exactly once from the queue's view."""
    try:
        inputs = json.loads((job_dir / INPUTS_FILE).read_text(encoding="utf-8"))
        report = await gemini_client.estimate_measurements(_load_photos(job_dir), **inputs)
    except Exception as e:   # anything uncaught here would leave the page waiting until it times out
        message = str(e) if isinstance(e, gemini_client.MeasurementError) else f"Measuring failed: {e}"
        (job_dir / ERROR_FILE).write_text(json.dumps({"error": message}), encoding="utf-8")
        return
    record = {
        "job_id": job_dir.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": config.MEASURE_MODEL,
        "inputs": inputs,
        "body_shape": report.body_shape.model_dump(),
        "measurements_cm": {k: round(v, 1) for k, v in report.measurements.model_dump().items()},
        "warnings": report.warnings,
    }
    (job_dir / MEASUREMENT_FILE).write_text(json.dumps(record, indent=2), encoding="utf-8")
