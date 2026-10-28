"""Job status records in Firestore: one document per measurement job, measurement_jobs/<job ID>.

The upload page waits for its measurement on one open connection (/job_events, Server-Sent Events).
The web server holding that connection listens to the job's record, and Firestore tells it the moment
a worker changes it, whichever machine the worker runs on. Nothing polls Firestore, and the web
servers don't need a message broker to hear back from workers.

    status       queued -> measuring -> ready | failed
    error        only when failed
    user_id, job_id, created_at, updated_at, expires_at

The record only says when to look: the result itself is measurement.json or error.json in the job
folder, written before the record changes. So if Firestore can't be reached, nothing breaks: writes
are skipped (and logged), and the event stream still finds the result by checking the folder every
few seconds.

A top-level collection, not one under users/<UID>, so the Firestore console lists everyone's jobs in one
place for debugging. Readers check user_id, so a user's stream never follows someone else's job.

expires_at: with the TTL policy set up (infra/terraform/firestore.tf, or the gcloud command in the
README), Firestore deletes a record after that time, usually within a day of it.

Uses google-cloud-firestore directly (it comes with firebase-admin), so it works with sign-in off too.
The client is synchronous, so writes run in a thread; listeners run on Firestore's own threads.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import config

log = logging.getLogger(__name__)

COLLECTION = "measurement_jobs"
STATUSES = ("queued", "measuring", "ready", "failed")
KEEP_FOR = timedelta(hours=24)   # expires_at = last change + this
WRITE_TIMEOUT_S = 5
# After a failed write, skip writes and listeners for this long, so an unreachable Firestore costs
# one timeout per minute rather than one per upload. The folder checks cover the gap.
RETRY_AFTER_S = 60

# Called with (status, error) when a job's record changes. error is None unless the status is "failed".
StatusHandler = Callable[[str, str | None], None]

_client = None
_skip_until = 0.0


def _available() -> bool:
    return config.JOB_STATUS_ENABLED and time.monotonic() >= _skip_until


def _pause(action: str, job_id: str, error: Exception) -> None:
    global _skip_until
    _skip_until = time.monotonic() + RETRY_AFTER_S
    log.warning("couldn't %s job status for %s (%s: %s). Skipping job status for %d s; pages fall back to "
                "checking the job folder.", action, job_id, type(error).__name__, error, RETRY_AFTER_S)


def _db():
    global _client
    if _client is None:
        from google.cloud import firestore   # imported here so modules that never use it don't pay for it
        _client = firestore.Client(project=config.FIREBASE_PROJECT_ID)
    return _client


def _write(user_id: str, job_id: str, status: str, error: str | None) -> None:
    from google.cloud import firestore
    data = {
        "user_id": user_id,
        "job_id": job_id,
        "status": status,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "expires_at": datetime.now(timezone.utc) + KEEP_FOR,
    }
    if status == "queued":
        data["created_at"] = firestore.SERVER_TIMESTAMP
    if status == "failed":
        data["error"] = error
    # Merged, so later writes keep created_at
    _db().collection(COLLECTION).document(job_id).set(data, merge=True, timeout=WRITE_TIMEOUT_S)


async def set_status(user_id: str, job_id: str, status: str, error: str | None = None) -> None:
    """Record a job's new status. Best effort: never raises, since the job folder is what counts."""
    if status not in STATUSES:
        raise ValueError(f"Unknown job status: {status!r}")
    if not _available():
        return
    try:
        await asyncio.to_thread(_write, user_id, job_id, status, error)
    except Exception as e:
        _pause("write", job_id, e)


def watch(user_id: str, job_id: str, on_status: StatusHandler) -> Callable[[], None]:
    """Call on_status whenever the job's record changes, and once straight away if it exists. It's called
    on one of Firestore's threads, not the caller's. Returns a function that stops listening. Blocks
    briefly while the listener starts, so call it in a thread from async code. If listening can't start,
    nothing is called and the returned function does nothing."""
    if not _available():
        return lambda: None

    def on_snapshot(snapshots, _changes, _read_time):
        for snap in snapshots:
            data = snap.to_dict() if snap.exists else None
            # Someone else's job with the same ID (job IDs are only unique per user): not ours
            if not data or data.get("user_id") != user_id:
                continue
            if data.get("status") in STATUSES:
                on_status(data["status"], data.get("error"))

    try:
        listener = _db().collection(COLLECTION).document(job_id).on_snapshot(on_snapshot)
    except Exception as e:
        _pause("listen to", job_id, e)
        return lambda: None

    def stop() -> None:
        try:
            listener.unsubscribe()
        except Exception:   # already closed, e.g. the listener failed on its own
            pass

    return stop
