"""Measurement jobs: everything about a job that doesn't depend on how it was delivered.
Used by the web server (in-process mode) and by worker.py (queue mode), so no FastAPI in here.

A job is a folder in RUNS_DIR holding the photos and inputs.json. Queue messages carry only
the job ID; the worker reads everything else from the folder. With workers on other machines,
RUNS_DIR has to be shared storage (e.g. a Cloud Storage bucket) rather than a local folder.
"""
import json
import mimetypes
from datetime import datetime
from pathlib import Path

import config
import gemini_client
import prompts

INPUTS_FILE = "inputs.json"
MEASUREMENT_FILE = "measurement.json"
ERROR_FILE = "error.json"


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
