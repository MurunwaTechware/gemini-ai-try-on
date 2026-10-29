"""FastAPI entry point.

Pages:  /login  ->  /  (upload + details)  ->  /measurements?job=ID  ->  /tryon?job=ID
API:    POST /process_upload, GET /job_events/{id} (SSE), GET /measurements_data/{id},
        GET /defaults, POST /try-on, POST /try-on/save; sign-in routes are in auth.py, /account and /me... in account.py,
        the user's try-on photos (/me/tryon-photos...) in tryon_photos.py
Everything except the sign-in routes and /static needs a signed-in user (auth.py). Each upload is a "job" with its own
folder, <user ID>/measurement_profiles/<job ID>. Generating a try-on keeps nothing; one the user saves goes in
<user ID>/saved_tryons/<ID>. Both are under storage.ROOT: a local folder or the Cloud Storage bucket (storage.py),
whose calls block, so async routes make them in a thread. The user ID always comes
from the session cookie, so one user can't reach another's jobs even with the job ID.
Measurement jobs run in this process or on queue workers, depending on config.QUEUE_BACKEND.
"""
import asyncio
import base64
import json
import mimetypes
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import account
import auth
import config
import gemini_client
import job_status
import jobs
import prompts
import tryon_photos
from auth import User, current_user, page_user, same_origin
from job_queue import JobQueue, create_queue
from storage import StoredPath

STATIC = Path(__file__).parent / "static"

MEASURE_TIMEOUT_S = 300   # the event stream gives up after this; a measurement normally takes ~30 s
PING_EVERY_S = 15         # keep-alive comment so browsers and proxies don't drop an idle stream
# The event stream is woken by the job's status record (job_status.py). It also checks the job folder
# this often, so it still finds the result if a change is missed, e.g. while Firestore is unreachable.
FOLDER_CHECK_S = 5
# A record says the job finished but its result file isn't there: wait this long for the file
# (shared storage can lag a moment) before reporting a failure
RESULT_GRACE_S = 3

# In-process mode only: wakes /job_events streams the moment a background job finishes, without
# waiting for Firestore. Keyed by "<user ID>/<job ID>", as are the other job sets here.
_waiters: dict[str, set[asyncio.Event]] = {}
# In-process mode only: jobs whose background task is running in this process
_inprocess_running: set[str] = set()
_queue: JobQueue | None = None


def _key(user_id: str, job_id: str) -> str:
    return f"{user_id}/{job_id}"


def _notify_done(user_id: str, job_id: str) -> None:
    for waiter in _waiters.get(_key(user_id, job_id), ()):
        waiter.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue
    auth.init()
    if config.QUEUE_BACKEND != "inprocess":
        _queue = create_queue()
        await _queue.connect()
    yield
    if _queue:
        await _queue.close()


app = FastAPI(title="Gemini try-on demo", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(tryon_photos.router)


@app.middleware("http")
async def _revalidate_pages(request: Request, call_next):
    """Pages and /static files: without a Cache-Control header, browsers guess how long to keep them and
    can show an old version for days after an update. no-cache still lets them keep a copy; they just
    check it's current first (a 304 with no body when it is). Responses that set their own are left alone."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.exception_handler(auth.LoginRequired)
async def _to_login(request: Request, exc: auth.LoginRequired):
    return RedirectResponse(auth.login_redirect_url(exc.next_path), status_code=303)


async def _read_image(upload: UploadFile, label: str, max_bytes: int = config.MAX_IMAGE_BYTES) -> bytes:
    if upload.content_type not in config.ALLOWED_MIME:
        raise HTTPException(415, f"{label} must be JPEG, PNG or WebP (got {upload.content_type}).")
    data = await upload.read()
    if not data:
        raise HTTPException(400, f"{label} is empty.")
    if len(data) > max_bytes:
        raise HTTPException(413, f"{label} is over {max_bytes // (1024 * 1024)} MB. Resize it and try again.")
    return data


def _ext(mime: str) -> str:
    return mimetypes.guess_extension(mime) or ".png"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _job_dir(user: User, job_id: str) -> StoredPath:
    """The signed-in user's job. Someone else's job ID gives 404, the same as one that doesn't exist,
    so the response doesn't confirm that it exists."""
    try:
        job_dir = jobs.job_path(user.uid, job_id)
    except ValueError:
        job_dir = None
    if job_dir is None or not job_dir.is_dir():
        raise HTTPException(404, "Unknown session. Start again from the upload page.")
    return job_dir


def _save_tryon(user: User, person, garments, prompt, result, job_id: str | None) -> str:
    # In the user's saved_tryons folder, apart from the measurement profiles. meta.json records which
    # profile it came from (null if none). The suffix keeps two saves in the same second apart.
    saved_id = f"{_timestamp()}-{secrets.token_hex(3)}"
    folder = jobs.user_dir(user.uid) / jobs.TRYONS_DIR / saved_id
    folder.mkdir(parents=True)
    (folder / jobs.TRYON_META_FILE).write_text(json.dumps({"measurement_profile": job_id, "model": config.MODEL}),
                                               encoding="utf-8")
    (folder / f"result{_ext(result.mime_type)}").write_bytes(result.data)
    (folder / f"person{_ext(person.mime_type)}").write_bytes(person.data)
    for slot, g in garments.items():
        (folder / f"{slot}{_ext(g.mime_type)}").write_bytes(g.data)
    (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
    return saved_id


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False, dependencies=[Depends(page_user)])
def upload_page():
    return FileResponse(STATIC / "screen1_upload.html")


@app.get("/measurements", include_in_schema=False, dependencies=[Depends(page_user)])
def measurements_page():
    return FileResponse(STATIC / "screen2_measurements.html")


@app.get("/tryon", include_in_schema=False, dependencies=[Depends(page_user)])
def tryon_page():
    return FileResponse(STATIC / "index.html")


# ── Measurements ──────────────────────────────────────────────────────────────

async def _measure_inprocess(user_id: str, job_dir: StoredPath) -> None:
    """In-process mode: runs after /process_upload has responded."""
    try:
        await jobs.run_measurement(user_id, job_dir)
    finally:
        # The result file is written before this, so a stream that wakes up always finds it
        _inprocess_running.discard(_key(user_id, job_dir.name))
        _notify_done(user_id, job_dir.name)


@app.post("/process_upload", dependencies=[Depends(same_origin)])
async def process_upload(
    background: BackgroundTasks,
    user: User = Depends(current_user),
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
    job_dir = jobs.job_path(user.uid, job_id)
    inputs = {"height_cm": height_cm, "weight_kg": weight_kg, "gender": gender, "age": age}

    def save():   # in a thread: with the bucket, each write is a network call
        job_dir.mkdir(parents=True)
        for view, p in photos.items():
            (job_dir / f"{view}{_ext(p.mime_type)}").write_bytes(p.data)
        (job_dir / jobs.INPUTS_FILE).write_text(json.dumps(inputs), encoding="utf-8")   # workers read it from here

    await asyncio.to_thread(save)

    # Recorded before the job can start, so a fast worker's "measuring" can't be overwritten by this
    await job_status.set_status(user.uid, job_id, "queued")
    if _queue:
        try:
            await _queue.publish_job(user.uid, job_id)
        except Exception:
            message = "The measuring service is unavailable right now. Please try again shortly."
            await job_status.set_status(user.uid, job_id, "failed", message)
            raise HTTPException(503, message)
    else:
        _inprocess_running.add(_key(user.uid, job_id))   # registered before responding, so the stream always finds it
        background.add_task(_measure_inprocess, user.uid, job_dir)
    return {"job_id": job_id}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _job_event_stream(user: User, job_dir: StoredPath):
    """Yields `status` events as the job moves on (`queued`, then `measuring`), then exactly one `ready` or
    `failed`, with keep-alive pings while waiting. Named `failed` rather than `error` because EventSource
    uses `error` for connection problems.

    What wakes it: the job's status record in Firestore, which a worker on any machine updates (and, in
    in-process mode, the background task finishing). The result files in the job folder are what count:
    the record only says when to look, and the folder is also checked every FOLDER_CHECK_S seconds in case
    a change is missed. The user was checked when the stream opened; it isn't rechecked while it stays open."""
    job_id = job_dir.name
    key = _key(user.uid, job_id)
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    record: dict = {}   # the newest status from the job's record: {"status": ..., "error": ...}

    def on_status(status: str, error: str | None) -> None:   # on the event loop, via call_soon_threadsafe
        record.update(status=status, error=error)
        wake.set()

    # Start listening before the folder checks, so a job finishing between the check and the wait still
    # wakes this stream; a job that finished earlier is caught by the folder check
    _waiters.setdefault(key, set()).add(wake)
    stop_watching = await asyncio.to_thread(
        job_status.watch, user.uid, job_id, lambda status, error: loop.call_soon_threadsafe(on_status, status, error))
    deadline = loop.time() + MEASURE_TIMEOUT_S
    last_sent = loop.time()
    sent_status = None      # the last `status` event sent, so each stage is sent once
    finished_at = None      # when the record first said ready/failed with no result file yet
    try:
        while True:
            wake.clear()   # before the checks: a change during them makes the wait below return at once
            # Read before the folder checks: the task writes its file before leaving this set,
            # so "not running" plus "no file" can only mean the task never finished
            running_here = key in _inprocess_running
            finished = await asyncio.to_thread(jobs.result, job_dir)   # network calls with the bucket
            if finished and finished[0] == "ready":
                yield _sse("ready", {"redirect": f"/measurements?job={job_id}"})
                return
            if finished:
                yield _sse("failed", finished[1])
                return
            if not _queue and not running_here:
                # In-process only: the task died, usually a server restart (--reload does this on
                # every file save). With a queue the job is still queued, and a worker picks it up
                yield _sse("failed", {"error": "Measuring was interrupted because the server restarted. Please upload again."})
                return

            status = record.get("status")
            if status in ("ready", "failed"):
                # The file is written before the record, so it should be there; give shared storage a moment
                finished_at = finished_at or loop.time()
                if loop.time() - finished_at >= RESULT_GRACE_S:
                    error = record.get("error") if status == "failed" else None
                    yield _sse("failed", {"error": error or "Measuring finished without a result. Please try again."})
                    return
            elif status and status != sent_status:
                sent_status = status
                last_sent = loop.time()
                yield _sse("status", {"status": status})

            if loop.time() > deadline:
                yield _sse("failed", {"error": "This is taking longer than expected. Please try again."})
                return
            if loop.time() - last_sent >= PING_EVERY_S:
                last_sent = loop.time()
                yield ": ping\n\n"   # lines starting with ":" are comments; EventSource ignores them
            try:
                await asyncio.wait_for(wake.wait(), timeout=1 if finished_at else FOLDER_CHECK_S)
            except TimeoutError:
                pass
    finally:
        stop_watching()
        waiters = _waiters.get(key)
        if waiters is not None:
            waiters.discard(wake)
            if not waiters:
                del _waiters[key]


@app.get("/job_events/{job_id}")
def job_events(job_id: str, user: User = Depends(current_user)):
    return StreamingResponse(_job_event_stream(user, _job_dir(user, job_id)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Replaced by /job_events (SSE). Kept for checking a job by hand in the browser; uncomment to use.
# @app.get("/job_status/{job_id}")
# def job_status(job_id: str, user: User = Depends(current_user)):
#     job_dir = _job_dir(user, job_id)
#     if (job_dir / jobs.MEASUREMENT_FILE).exists():
#         return {"status": "ready", "redirect": f"/measurements?job={job_id}"}
#     if (job_dir / jobs.ERROR_FILE).exists():
#         return {"status": "error", "error": json.loads((job_dir / jobs.ERROR_FILE).read_text(encoding="utf-8"))["error"]}
#     return {"status": "processing"}


@app.get("/measurements_data/{job_id}")
def measurements_data(job_id: str, user: User = Depends(current_user)):
    path = _job_dir(user, job_id) / jobs.MEASUREMENT_FILE
    if not path.exists():
        raise HTTPException(404, "Measurements aren't ready for this session yet.")
    return json.loads(path.read_text(encoding="utf-8"))


# ── Try-on ────────────────────────────────────────────────────────────────────

@app.get("/defaults", dependencies=[Depends(current_user)])
def defaults():
    return {"prompt": prompts.DEFAULT_RULES, "model": config.MODEL, "max_outputs": config.MAX_OUTPUTS}


async def _tryon_inputs(person: UploadFile, uploads: dict[str, UploadFile | None]):
    """The person photo and the items, checked. Shared by generating and saving."""
    uploads = {slot: f for slot, f in uploads.items() if f is not None and f.filename}
    if not uploads:
        raise HTTPException(400, "Add at least one item: a top, bottom, headwear, or eyewear.")
    person_img = gemini_client.InputImage(await _read_image(person, "Person photo"), person.content_type)
    garments = {
        slot: gemini_client.InputImage(await _read_image(f, f"{slot.capitalize()} photo"), f.content_type)
        for slot, f in uploads.items()
    }
    return person_img, garments


def _tryon_prompt(garments: dict, rules: str) -> str:
    """The full prompt: the same for generating and saving, so prompt.txt is what produced the result."""
    return prompts.build_prompt([s for s in config.GARMENT_SLOTS if s in garments], rules.strip() or prompts.DEFAULT_RULES)


@app.post("/try-on", dependencies=[Depends(same_origin)])
async def try_on(
    user: User = Depends(current_user),
    person: UploadFile = File(...),
    top: UploadFile | None = File(None),
    bottom: UploadFile | None = File(None),
    headwear: UploadFile | None = File(None),
    eyewear: UploadFile | None = File(None),
    prompt: str = Form(""),
    count: int = Form(1),
):
    """Generate results and send them to the page. Nothing is kept: the user saves the one they want
    with POST /try-on/save."""
    if not 1 <= count <= config.MAX_OUTPUTS:
        raise HTTPException(400, f"count must be between 1 and {config.MAX_OUTPUTS}.")
    person_img, garments = await _tryon_inputs(
        person, {"top": top, "bottom": bottom, "headwear": headwear, "eyewear": eyewear})
    total = len(person_img.data) + sum(len(g.data) for g in garments.values())
    if total > config.MAX_TOTAL_BYTES:
        raise HTTPException(413, "The photos together are over 14 MB. Resize one or more and try again.")

    results = await gemini_client.run_tryon(person_img, garments, _tryon_prompt(garments, prompt), count)

    if not any(r.image for r in results):
        # Every slot failed: surface the first reason so the page can show it
        raise HTTPException(502, results[0].error or "The model returned no images.")

    return {
        "model": config.MODEL,
        "garments": list(garments),
        "results": [
            {"ok": True, "mime_type": r.mime_type, "data": base64.b64encode(r.image).decode(),
             "model_text": r.model_text}
            if r.image else {"ok": False, "error": r.error}
            for r in results
        ],
    }


@app.post("/try-on/save", dependencies=[Depends(same_origin)])
async def save_try_on(
    user: User = Depends(current_user),
    result: UploadFile = File(...),
    person: UploadFile = File(...),
    top: UploadFile | None = File(None),
    bottom: UploadFile | None = File(None),
    headwear: UploadFile | None = File(None),
    eyewear: UploadFile | None = File(None),
    prompt: str = Form(""),
    job: str = Form(""),   # set when the try-on page was reached from a measurement profile
):
    """Keep the result the user picked, with what it was made from. The page sends it back with the same
    photos and rules it was generated from, since generating keeps nothing. Everything lands in the user's
    own folder, so sending a different image only affects their own saved try-ons."""
    job_id = (await asyncio.to_thread(_job_dir, user, job)).name if job else None
    person_img, garments = await _tryon_inputs(
        person, {"top": top, "bottom": bottom, "headwear": headwear, "eyewear": eyewear})
    result_img = gemini_client.InputImage(
        await _read_image(result, "The result", config.MAX_RESULT_BYTES), result.content_type)
    saved_id = await asyncio.to_thread(
        _save_tryon, user, person_img, garments, _tryon_prompt(garments, prompt), result_img, job_id)
    return {"saved_id": saved_id}


# Assets the pages load (drum picker); pages themselves are served by the routes above
app.mount("/static", StaticFiles(directory=STATIC), name="static")
