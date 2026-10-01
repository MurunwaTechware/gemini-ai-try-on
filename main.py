"""FastAPI entry point.

Pages:  /  (upload + details)  ->  /measurements?job=ID  ->  /tryon?job=ID
API:    POST /process_upload, GET /job_events/{id} (SSE), GET /measurements_data/{id},
        GET /defaults, POST /try-on
Each upload is a "job" with its own folder in RUNS_DIR; its try-on runs are saved inside it.
Measurement jobs run in this process or on queue workers, depending on config.QUEUE_BACKEND.
"""
import asyncio
import base64
import json
import mimetypes
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
import gemini_client
import jobs
import prompts
from job_queue import JobQueue, create_queue

STATIC = Path(__file__).parent / "static"
_JOB_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")   # also stops path tricks like "../"

MEASURE_TIMEOUT_S = 300   # the event stream gives up after this; a measurement normally takes ~30 s
PING_EVERY_S = 15         # keep-alive comment so browsers and proxies don't drop an idle stream

# Wakes /job_events streams the moment a job finishes. In-process mode calls _notify_done itself;
# queue mode calls it for each "done" event from the workers, whichever machine ran the job.
_waiters: dict[str, asyncio.Event] = {}
# In-process mode only: jobs whose background task is running in this process
_inprocess_running: set[str] = set()
_queue: JobQueue | None = None


def _notify_done(job_id: str) -> None:
    waiter = _waiters.pop(job_id, None)
    if waiter:
        waiter.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue
    if config.QUEUE_BACKEND == "rabbitmq":
        _queue = create_queue()
        await _queue.connect()
        await _queue.subscribe_done(_notify_done)
    yield
    if _queue:
        await _queue.close()


app = FastAPI(title="Gemini try-on demo", lifespan=lifespan)


async def _read_image(upload: UploadFile, label: str) -> bytes:
    if upload.content_type not in config.ALLOWED_MIME:
        raise HTTPException(415, f"{label} must be JPEG, PNG or WebP (got {upload.content_type}).")
    data = await upload.read()
    if not data:
        raise HTTPException(400, f"{label} is empty.")
    if len(data) > config.MAX_IMAGE_BYTES:
        raise HTTPException(413, f"{label} is over 7 MB. Resize it and try again.")
    return data


def _ext(mime: str) -> str:
    return mimetypes.guess_extension(mime) or ".png"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _job_dir(job_id: str) -> Path:
    job_dir = config.RUNS_DIR / job_id
    if not _JOB_ID.fullmatch(job_id) or not job_dir.is_dir():
        raise HTTPException(404, "Unknown session. Start again from the upload page.")
    return job_dir


def _save_run(person, garments, prompt, results, job_id: str | None) -> str:
    # Inside the job folder when the try-on came from a measurement session
    run_id = f"{job_id}/tryon-{_timestamp()}" if job_id else _timestamp()
    run_dir = config.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"person{_ext(person.mime_type)}").write_bytes(person.data)
    for slot, g in garments.items():
        (run_dir / f"{slot}{_ext(g.mime_type)}").write_bytes(g.data)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    for i, r in enumerate(results, 1):
        if r.image:
            (run_dir / f"result_{i}{_ext(r.mime_type)}").write_bytes(r.image)
    return run_id


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def upload_page():
    return FileResponse(STATIC / "screen1_upload.html")


@app.get("/measurements", include_in_schema=False)
def measurements_page():
    return FileResponse(STATIC / "screen2_measurements.html")


@app.get("/tryon", include_in_schema=False)
def tryon_page():
    return FileResponse(STATIC / "index.html")


# ── Measurements ──────────────────────────────────────────────────────────────

async def _measure_inprocess(job_dir: Path) -> None:
    """In-process mode: runs after /process_upload has responded."""
    try:
        await jobs.run_measurement(job_dir)
    finally:
        # The result file is written before this, so a stream that wakes up always finds it
        _inprocess_running.discard(job_dir.name)
        _notify_done(job_dir.name)


@app.post("/process_upload")
async def process_upload(
    background: BackgroundTasks,
    front: UploadFile = File(...),
    left: UploadFile = File(...),
    right: UploadFile = File(...),
    height_cm: float = Form(...),
    weight_kg: float = Form(...),
    gender: str = Form(...),
    age: int = Form(...),
):
    # Same limits as the page's own checks, since requests can come from elsewhere
    if not 90 <= height_cm <= 245:
        raise HTTPException(400, "Height must be between 90 and 245 cm.")
    if not 30 <= weight_kg <= 200:
        raise HTTPException(400, "Weight must be between 30 and 200 kg.")
    if not 1 <= age <= 120:
        raise HTTPException(400, "Age must be between 1 and 120.")
    if gender not in ("male", "female", "neutral"):
        raise HTTPException(400, "Please select a gender option.")

    uploads = {"front": front, "left": left, "right": right}
    photos = {view: gemini_client.InputImage(await _read_image(f, f"{view.capitalize()} photo"), f.content_type)
              for view, f in uploads.items()}
    if sum(len(p.data) for p in photos.values()) > config.MAX_TOTAL_BYTES:
        raise HTTPException(413, "The photos together are over 14 MB. Resize one or more and try again.")

    job_id = f"{_timestamp()}-{secrets.token_hex(3)}"
    job_dir = config.RUNS_DIR / job_id
    job_dir.mkdir(parents=True)
    for view, p in photos.items():
        (job_dir / f"{view}{_ext(p.mime_type)}").write_bytes(p.data)

    inputs = {"height_cm": height_cm, "weight_kg": weight_kg, "gender": gender, "age": age}
    (job_dir / jobs.INPUTS_FILE).write_text(json.dumps(inputs), encoding="utf-8")   # workers read it from here

    if _queue:
        try:
            await _queue.publish_job(job_id)
        except Exception:
            raise HTTPException(503, "The measuring service is unavailable right now. Please try again shortly.")
    else:
        _inprocess_running.add(job_id)   # registered before responding, so the stream always finds it
        background.add_task(_measure_inprocess, job_dir)
    return {"job_id": job_id}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _job_event_stream(job_dir: Path):
    """Yields exactly one `ready` or `failed` event, with keep-alive pings while waiting.
    Named `failed` rather than `error` because EventSource uses `error` for connection problems."""
    job_id = job_dir.name
    # Registered before the disk checks, so a job finishing between the check and the wait
    # still wakes this stream; a job that finished earlier is caught by the disk check
    waiter = _waiters.setdefault(job_id, asyncio.Event())
    deadline = asyncio.get_running_loop().time() + MEASURE_TIMEOUT_S
    while True:
        # Read before the disk checks: the task writes its file before leaving this set,
        # so "not running" plus "no file" can only mean the task never finished
        running_here = job_id in _inprocess_running
        if (job_dir / jobs.MEASUREMENT_FILE).exists():
            _waiters.pop(job_id, None)
            yield _sse("ready", {"redirect": f"/measurements?job={job_id}"})
            return
        if (job_dir / jobs.ERROR_FILE).exists():
            _waiters.pop(job_id, None)
            yield _sse("failed", json.loads((job_dir / jobs.ERROR_FILE).read_text(encoding="utf-8")))
            return
        if not _queue and not running_here:
            # In-process only: the task died, usually a server restart (--reload does this on
            # every file save). With a queue the job is still queued, and a worker picks it up
            yield _sse("failed", {"error": "Measuring was interrupted because the server restarted. Please upload again."})
            return
        if waiter.is_set():   # woken, but no result file: would otherwise spin until the deadline
            yield _sse("failed", {"error": "Measuring finished without a result. Please try again."})
            return
        if asyncio.get_running_loop().time() > deadline:
            yield _sse("failed", {"error": "This is taking longer than expected. Please try again."})
            return
        try:
            await asyncio.wait_for(waiter.wait(), timeout=PING_EVERY_S)
        except TimeoutError:
            yield ": ping\n\n"   # lines starting with ":" are comments; EventSource ignores them


@app.get("/job_events/{job_id}")
def job_events(job_id: str):
    return StreamingResponse(_job_event_stream(_job_dir(job_id)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Replaced by /job_events (SSE). Kept for checking a job by hand in the browser; uncomment to use.
# @app.get("/job_status/{job_id}")
# def job_status(job_id: str):
#     job_dir = _job_dir(job_id)
#     if (job_dir / jobs.MEASUREMENT_FILE).exists():
#         return {"status": "ready", "redirect": f"/measurements?job={job_id}"}
#     if (job_dir / jobs.ERROR_FILE).exists():
#         return {"status": "error", "error": json.loads((job_dir / jobs.ERROR_FILE).read_text(encoding="utf-8"))["error"]}
#     return {"status": "processing"}


@app.get("/measurements_data/{job_id}")
def measurements_data(job_id: str):
    path = _job_dir(job_id) / jobs.MEASUREMENT_FILE
    if not path.exists():
        raise HTTPException(404, "Measurements aren't ready for this session yet.")
    return json.loads(path.read_text(encoding="utf-8"))


# ── Try-on ────────────────────────────────────────────────────────────────────

@app.get("/defaults")
def defaults():
    return {"prompt": prompts.DEFAULT_RULES, "model": config.MODEL, "max_outputs": config.MAX_OUTPUTS}


@app.post("/try-on")
async def try_on(
    person: UploadFile = File(...),
    top: UploadFile | None = File(None),
    bottom: UploadFile | None = File(None),
    headwear: UploadFile | None = File(None),
    eyewear: UploadFile | None = File(None),
    prompt: str = Form(""),
    count: int = Form(1),
    job: str = Form(""),   # set when the try-on page was reached from a measurement session
):
    if not 1 <= count <= config.MAX_OUTPUTS:
        raise HTTPException(400, f"count must be between 1 and {config.MAX_OUTPUTS}.")
    job_id = _job_dir(job).name if job else None

    uploads = {"top": top, "bottom": bottom, "headwear": headwear, "eyewear": eyewear}
    uploads = {slot: f for slot, f in uploads.items() if f is not None and f.filename}
    if not uploads:
        raise HTTPException(400, "Add at least one item: a top, bottom, headwear, or eyewear.")

    person_img = gemini_client.InputImage(await _read_image(person, "Person photo"), person.content_type)
    garments = {
        slot: gemini_client.InputImage(await _read_image(f, f"{slot.capitalize()} photo"), f.content_type)
        for slot, f in uploads.items()
    }
    total = len(person_img.data) + sum(len(g.data) for g in garments.values())
    if total > config.MAX_TOTAL_BYTES:
        raise HTTPException(413, "The photos together are over 14 MB. Resize one or more and try again.")

    rules = prompt.strip() or prompts.DEFAULT_RULES
    prompt = prompts.build_prompt([s for s in config.GARMENT_SLOTS if s in garments], rules)
    results = await gemini_client.run_tryon(person_img, garments, prompt, count)

    if not any(r.image for r in results):
        # Every slot failed: surface the first reason so the page can show it
        raise HTTPException(502, results[0].error or "The model returned no images.")

    run_id = _save_run(person_img, garments, prompt, results, job_id)

    return {
        "run_id": run_id,
        "model": config.MODEL,
        "garments": list(garments),
        "results": [
            {"ok": True, "mime_type": r.mime_type, "data": base64.b64encode(r.image).decode(),
             "model_text": r.model_text}
            if r.image else {"ok": False, "error": r.error}
            for r in results
        ],
    }


# Assets the pages load (drum picker); pages themselves are served by the routes above
app.mount("/static", StaticFiles(directory=STATIC), name="static")
