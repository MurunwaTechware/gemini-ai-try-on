# gemini-ai-try-on

A web app that estimates body measurements from photos and lets people virtually try on clothing, using Google Gemini on Vertex AI.

1. **Upload**: front, left-side and right-side photos, plus height, weight, gender and age.
2. **Measurements**: Gemini estimates body shape and 16 measurements in cm. These are AI estimates, not tailor-grade measurements.
3. **Try-on**: pick any photo of the person, add a top, bottom, headwear and/or eyewear, and generate up to 4 variations.

## Quick start (Docker)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/), the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), and access to a Google Cloud project with Vertex AI (see [Access](#access)).

```
# 1. Log in to Google Cloud (opens a browser) and bill usage to the project
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID

# 2. Create your settings and set GOOGLE_CLOUD_PROJECT in it
cp .env.example .env            # Command Prompt (cmd): copy .env.example .env

# 3. Start RabbitMQ, the web server and a worker
docker compose up --build
```

Open **http://localhost:8000**. Ignore the `0.0.0.0` address in the logs; see [Troubleshooting](#the-browser-says-err_address_invalid).

| Task | Command |
|---|---|
| Stop | `Ctrl+C`, or `docker compose down` if started with `-d` |
| Run more workers | `docker compose up --build --scale worker=3` |
| RabbitMQ dashboard | http://localhost:15673 (guest / guest) |
| Pick up code changes | `docker compose up --build` |

## Running without Docker

Requires Python 3.14 (what the Docker image uses; older versions are untested).

```
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

By default this runs in **in-process** mode: measurements run inside the web server, and there's nothing else to start. To use the queue instead, set `QUEUE_BACKEND=rabbitmq` in `.env`, start a broker (`docker compose up -d rabbitmq` starts this project's broker on port 5673), and run `python worker.py` in a second terminal.

## Configuration

Settings are read from `.env`. Only the first one is required.

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | (required) | Google Cloud project that runs and pays for the Gemini calls |
| `GOOGLE_CLOUD_LOCATION` | `global` | Vertex AI location |
| `GEMINI_API_KEY` | (unset) | Use an AI Studio API key instead of Vertex AI. Not needed with Google Cloud login |
| `GEMINI_IMAGE_MODEL` | `gemini-3.1-flash-image` | Try-on model |
| `GEMINI_MEASURE_MODEL` | `gemini-2.5-pro` | Measurement model |
| `QUEUE_BACKEND` | `inprocess` | `inprocess` or `rabbitmq`. Docker Compose always uses `rabbitmq` |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | Broker address when running without Docker. Use port `5673` for this project's Compose broker |
| `WORKER_CONCURRENCY` | `4` | Jobs one worker runs at once. Keep workers × this within your Gemini quota |
| `RUNS_DIR` | `results/` next to `main.py` | Where uploads and results are saved |

Docker Compose only:

| Variable | Default | Purpose |
|---|---|---|
| `WEB_PORT` | `8000` | Port the app is served on |
| `RESULTS_DIR` | `./results` | Folder on your computer for uploads and results |
| `GCLOUD_ADC` | Found automatically | Path to the Google login file, if it isn't in the default place |

## Access

The app logs in to Google Cloud with your own account (Application Default Credentials); there's no API key. Organisation policy may block API keys anyway.

To let someone use the app with your project and its credits, the project Owner grants them two roles:

```
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:THEIR_EMAIL" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:THEIR_EMAIL" --role="roles/serviceusage.serviceUsageConsumer"
```

| Role | Shown in the console as | Why |
|---|---|---|
| `roles/aiplatform.user` | **Agent Platform User** (older pages: Vertex AI User) | Lets them call Gemini |
| `roles/serviceusage.serviceUsageConsumer` | **Service Usage Consumer** | Lets their login bill usage to your project |

Don't use **AI Platform Admin** (`roles/ml.admin`). Despite the name, it's for Google's retired AI Platform and doesn't include Gemini access. Avoid Owner or Editor too: those allow changing or deleting anything in the project. Usage by everyone counts against the project's billing account, so a budget alert (Billing → Budgets & alerts) is worth setting.

## How it works

```
Browser ──upload──► web server ──saves photos──► results/<job>/
                        │
                        └──job ID──► RabbitMQ [measure_jobs] ──► worker ──► Gemini
                                                                   │
Browser ◄──"ready" (SSE)── web server ◄──"done"── [job_events] ◄────┘
```

- The upload page waits on one open connection (`/job_events/<job>`, Server-Sent Events) and the server pushes `ready` or `failed` when the measurement finishes.
- Queue messages carry only the job ID. The worker reads the photos from the job folder, so the web server and workers must share `results/`.
- `job_queue.py` defines a small `JobQueue` interface. RabbitMQ is the only implementation for now; Google Pub/Sub or Cloud Tasks would be another class with the same methods.

Each upload gets its own folder:

```
results/<YYYYMMDD-HHMMSS-xxxxxx>/
├── front.*, left.*, right.*
├── inputs.json                    height, weight, gender, age
├── measurement.json               measurements in cm, body shape, warnings
├── error.json                     only if measuring failed
└── tryon-<YYYYMMDD-HHMMSS>/       one folder per try-on
    ├── person.*, top.*, …         the photos used
    ├── prompt.txt                 the exact prompt sent
    └── result_N.*
```

`results/` holds real people's photos and measurements. It's in `.gitignore`; keep it out of the repo and out of anything you share.

| File | What it does |
|---|---|
| `main.py` | Web server: pages, uploads, event stream, try-on |
| `worker.py` | Queue worker: takes measurement jobs and runs them |
| `jobs.py` | Measurement job logic, shared by the web server and worker |
| `job_queue.py` | Queue interface and its RabbitMQ implementation |
| `gemini_client.py` | Gemini calls; no web code |
| `prompts.py` | All prompt text |
| `config.py` | Settings from the environment |
| `static/` | The three pages and the height picker |

## Troubleshooting

### The browser says `ERR_ADDRESS_INVALID`
The log line `Uvicorn running on http://0.0.0.0:8000` describes where the server *listens* (every network interface), not an address to visit. Browsers block `0.0.0.0`. Open **http://localhost:8000** or **http://127.0.0.1:8000** instead. From another device on the same network, use the host computer's IP address, e.g. `http://192.168.1.25:8000`.

### `[Errno 21] Is a directory: '/gcloud/adc.json'`
Docker couldn't find your Google login file, so it mounted an empty folder in its place. Usually you haven't run `gcloud auth application-default login` yet, or Docker is running from WSL or with `sudo`, where the default path points somewhere else.

Docker also leaves an empty *folder* where the file should be, which stops `gcloud` from creating the real file. Remove it, log in, and restart:

```
# Windows PowerShell (no -Recurse: this only removes it if it's empty)
Remove-Item "$env:APPDATA\gcloud\application_default_credentials.json"
# macOS / Linux
rmdir ~/.config/gcloud/application_default_credentials.json

gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
docker compose down
docker compose up --build
```

If your login file is somewhere unusual, set `GCLOUD_ADC` in `.env` to its full path.

### `403 PERMISSION_DENIED` on `aiplatform.endpoints.predict`
Your account isn't allowed to call Gemini in the project. In order of likelihood:
1. **The role is missing or wrong.** It must be **Agent Platform User** (`roles/aiplatform.user`), not AI Platform Admin. See [Access](#access).
2. **The role was just granted.** Changes usually apply within a minute or two, occasionally longer. Retry with a new upload.
3. **You're logged in with a different account.** Check with `gcloud auth list`, then run `gcloud auth application-default login` again with the right one.

### `404 NOT_FOUND`: model "was not found or your project does not have access to it"
Some models appear in Vertex AI but can't be used in every project. For example, `gemini-3-pro` returns 404 in our project. Set `GEMINI_MEASURE_MODEL` or `GEMINI_IMAGE_MODEL` in `.env` to a model that works for you.

### "API keys are disallowed" in the Google Cloud console
Your organisation's security policy blocks API keys. You don't need one: the app uses your Google Cloud login (see [Quick start](#quick-start-docker)).

### `RabbitMQ unreachable … Is the broker running?`
Also shows as `refused the network connection` or `Server connection unexpectedly closed`. The web server or worker couldn't reach RabbitMQ: it's stopped, still starting up, or on another port. Both retry for about a minute at startup before giving up.
- **With Docker Compose:** this shouldn't happen, because the app waits for RabbitMQ's health check. Check `docker compose ps`.
- **Without Docker:** start the broker (`docker compose up -d rabbitmq`) and make sure `RABBITMQ_URL` uses port **5673**.

### Nothing shows on the RabbitMQ dashboard
This project's broker dashboard is **http://localhost:15673**. Port 15672 belongs to any other RabbitMQ on your computer.

With one job at a time the graphs barely move: **Ready** stays at 0 because a worker is already waiting, and **Unacked** shows 1 while Gemini is working (about 30 seconds). To watch jobs queue up, run `docker compose stop worker`, upload a few times, then `docker compose start worker`.

### `measure_jobs` or `job_events` appeared in another project's RabbitMQ
The app creates these on whatever broker `RABBITMQ_URL` points to. Port 5672 is the usual default for *other* RabbitMQ installs. Point `.env` at port **5673**, then remove the leftovers from the other broker:

```
docker exec <other-rabbitmq-container> rabbitmqctl delete_queue measure_jobs
docker exec <other-rabbitmq-container> rabbitmqctl delete_exchange job_events
```

Check first that `measure_jobs` has 0 messages (dashboard, or `rabbitmqctl list_queues name messages consumers`).

### Port 8000 is in use, or the page says `{"detail":"Not Found"}`
Another app is already using port 8000. A different app's server answering is what produces that "Not Found". Use another port: set `WEB_PORT=8001` in `.env` (Docker), or add `--port 8001` to the `uvicorn` command (no Docker), then open http://localhost:8001.

### "Measuring was interrupted because the server restarted"
Without Docker in in-process mode, measurements run inside the web server, so a restart cancels them. `--reload` restarts it on every file save. Upload again. Queue mode avoids this, because jobs wait in RabbitMQ until a worker finishes them.

### "The model blocked this image for safety reasons"
Usually a photo is in the wrong slot. For example, sunglasses under **Top** tell the model to replace the person's top with sunglasses, which trips the safety filter. Put each item in its own slot: sunglasses under **Eyewear**, caps under **Headwear**.

### Job folder names are 2 hours off
Inside Docker the clock is UTC, so job IDs from Docker use UTC while jobs run without Docker use your local time. Both kinds end up in `results/`, so sorting by name can mislead. This is a known issue, not yet fixed.

### `Form data requires "python-multipart" to be installed`
Some packages are missing (running without Docker). Run `pip install -r requirements.txt`.
