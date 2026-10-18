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

# 2. Create your settings and set GOOGLE_CLOUD_PROJECT and FIREBASE_API_KEY in it
#    (FIREBASE_API_KEY: see Sign-in below; it's a one-time Firebase setup)
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

Settings are read from `.env`. Only the first two are required.

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | (required) | Google Cloud project that runs and pays for the Gemini calls |
| `FIREBASE_API_KEY` | (required) | Firebase web API key for sign-in. Public by design, but keep it in `.env` with the rest |
| `FIREBASE_PROJECT_ID` | `GOOGLE_CLOUD_PROJECT` | Firebase project, if it's a different one |
| `FIREBASE_AUTH_DOMAIN` | `<project>.firebaseapp.com` | Where Google's sign-in window comes from |
| `SESSION_DAYS` | `5` | How long a sign-in lasts (Firebase allows up to 14) |
| `COOKIE_SECURE` | `true` | Send the session cookie over HTTPS only. Set `false` only for Safari on `http://localhost` |
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

To let someone run the app with your project and its credits, the project Owner grants them four roles:

```
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:THEIR_EMAIL" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:THEIR_EMAIL" --role="roles/serviceusage.serviceUsageConsumer"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:THEIR_EMAIL" --role="roles/firebaseauth.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:THEIR_EMAIL" --role="roles/datastore.user"
```

| Role | Shown in the console as | Why |
|---|---|---|
| `roles/aiplatform.user` | **Agent Platform User** (older pages: Vertex AI User) | Lets them call Gemini |
| `roles/serviceusage.serviceUsageConsumer` | **Service Usage Consumer** | Lets their login bill usage to your project |
| `roles/firebaseauth.admin` | **Firebase Authentication Admin** | Lets the server they run create and check sign-in sessions. Only needed by people running the server, not by people signing in to it |
| `roles/datastore.user` | **Cloud Datastore User** | Lets the server they run read and write user records in Firestore. Also only for people running the server |

Don't use **AI Platform Admin** (`roles/ml.admin`). Despite the name, it's for Google's retired AI Platform and doesn't include Gemini access. Avoid Owner or Editor too: those allow changing or deleting anything in the project. Usage by everyone counts against the project's billing account, so a budget alert (Billing → Budgets & alerts) is worth setting.

## Sign-in

Every page needs a sign-in (Firebase Authentication): **Continue with Google**, or an email and password. `/login` and `/account` are plain placeholder pages until the real frontend has its own.

One-time setup, by a project Owner:

1. In the [Firebase console](https://console.firebase.google.com/), **Add project** and pick the existing Google Cloud project.
2. **Authentication → Get started**. Until this is done, sign-in fails with `auth/configuration-not-found`.
3. **Authentication → Sign-in method**: enable **Google** and **Email/Password** (leave "Email link" off).
4. **Project settings → Your apps → Add app → Web**. Copy `apiKey` from the config it shows into `.env` as `FIREBASE_API_KEY`.
5. Under **Authentication → Settings → Authorized domains**, check that `localhost` is listed (newer projects may not include it) and add it if not. Add `127.0.0.1` too if you open the app at `http://127.0.0.1:8000`; otherwise Google sign-in fails there with `auth/unauthorized-domain`. Add any other address the app runs on too, e.g. the Cloud Run URL. Enter the hostname only, without `http://` or a port.
6. Create the Firestore database for user records. Pick the location carefully: it can't be changed later. `africa-south1` keeps South African users' data in the country.
   ```
   gcloud services enable firestore.googleapis.com --project YOUR_PROJECT_ID
   gcloud firestore databases create --project YOUR_PROJECT_ID --location africa-south1
   ```

The server checks sign-ins and saves user records with the same Google Cloud login as the Gemini calls, so the `set-quota-project` step in the quick start is needed here too. Firebase Authentication is free up to 50,000 monthly active users, and Firestore's free daily allowance easily covers one read and write per sign-in.

How it fits together:

1. `/login` signs in through the Firebase JS SDK, which gives the page a short-lived Firebase ID token.
2. The page sends it to `POST /session`. The server verifies it, saves the user's record in Firestore (`users/<user ID>`: email, name and surname, contact number, Google profile picture, terms accepted), and sets its own **HttpOnly session cookie** (scripts can't read it). The page keeps nothing else.
3. The browser sends the cookie with every request, including the `/job_events` stream, so no route needs tokens in headers or URLs.
4. Every route takes the user ID from the cookie. Files are stored under `results/<user ID>/`, so a job ID from someone else's link gives 404.

The account page (`/account`, or click your name in the top bar) has three sections:

- **Account Info**: email; password (change it, or add one to a Google account); Continue with Google (connect it to a password account); name, surname and contact number, each edited in place. **Download information** saves everything the app keeps about the user as one JSON file.
- **Measurement Profiles**: every upload, newest first, with links to its measurements and to try-on.
- **Saved Try-ons**: each result the user saved, what it was made from, and the profile it came from.

The name and surname start from the sign-in's name, split at the first space ("Jan van der Merwe" → "Jan", "van der Merwe"). Users can correct them; later sign-ins don't overwrite them.

Email and password accounts:

- Firebase stores and checks the passwords and sends the emails (verification, password reset). The app never sees a password.
- A new account can't sign in until its email is verified: the server refuses the session. Signing in before then sends a fresh link.
- One account per email. Someone who signed up with Google can add a password on `/account`, and someone who signed up with a password can connect Google there. Either way it stays the same account and user ID, so their measurements and try-ons stay with it. Firebase only adds a sign-in method for someone who has just signed in, so the page asks them to confirm with the method they already have.
- Firebase doesn't reveal whether an email has an account (email enumeration protection, on by default). So a wrong password or a Google-only account both get the same message, with a hint to try Continue with Google.
- Verification and reset emails come from `noreply@<project>.firebaseapp.com` and may land in spam. The wording and sender name are under **Authentication → Templates**.

Requests that change something (`/session`, `/logout`, `/process_upload`, `/try-on`, `/try-on/save`, `PATCH /me`) must come from the app's own pages: the server checks the `Origin` header. If the frontend moves to a different domain from this server, that check and the cookie settings need revisiting.

## How it works

```
Browser ──upload──► web server ──saves photos──► results/<user>/<job>/
                        │
                        └──job ID──► RabbitMQ [measure_jobs] ──► worker ──► Gemini
                                                                   │
Browser ◄──"ready" (SSE)── web server ◄──"done"── [job_events] ◄────┘
```

- The upload page waits on one open connection (`/job_events/<job>`, Server-Sent Events) and the server pushes `ready` or `failed` when the measurement finishes.
- Queue messages carry only the user ID and job ID. The worker reads the photos from the job folder, so the web server and workers must share `results/`.
- `job_queue.py` defines a small `JobQueue` interface. RabbitMQ is the only implementation for now; Google Pub/Sub or Cloud Tasks would be another class with the same methods.

Each user has a folder holding their measurement profiles (one per upload) and the try-ons they saved:

```
results/<user ID>/
├── measurement_profiles/
│   └── <YYYYMMDD-HHMMSS-xxxxxx>/      one folder per upload; its name is the job ID
│       ├── front.*, left.*, right.*
│       ├── inputs.json                height, weight, gender, age
│       ├── measurement.json           measurements in cm, body shape, warnings
│       └── error.json                 only if measuring failed
└── saved_tryons/
    └── <YYYYMMDD-HHMMSS-xxxxxx>/      one folder per save; the name is when it was saved
        ├── result.*                   the result the user picked
        ├── person.*, top.*, …         the photos it was made from
        ├── prompt.txt                 the exact prompt that produced it
        └── meta.json                  which measurement profile it came from (null if none), model
```

Generating a try-on keeps nothing: the results go to the page only. **Save to my account** sends the chosen result back with the photos and rules it was made from, and the server writes the folder. The server rebuilds the prompt from the same rules, so `prompt.txt` matches what produced the result.

Folders from before sign-in (directly under `results/`) and from before this layout (`results/<user ID>/<job ID>/`, `measurement-profiles/`, `try-ons/`) aren't picked up by the app.

`results/` holds real people's photos and measurements. It's in `.gitignore`; keep it out of the repo and out of anything you share.

| File | What it does |
|---|---|
| `main.py` | Web server: pages, uploads, event stream, try-on |
| `auth.py` | Sign-in: session cookie, `/login`, `/session`, `/logout` |
| `account.py` | `/account` and the user's own data: details, sign-in methods, profiles, saved try-ons, photos, download |
| `user_store.py` | User records in Firestore, saved at each sign-in |
| `models/Entities/Users.py` | The `User` record: what the app keeps about a user beyond Firebase's sign-in data |
| `worker.py` | Queue worker: takes measurement jobs and runs them |
| `jobs.py` | Measurement job logic, shared by the web server and worker |
| `job_queue.py` | Queue interface and its RabbitMQ implementation |
| `gemini_client.py` | Gemini calls; no web code |
| `prompts.py` | All prompt text |
| `config.py` | Settings from the environment |
| `static/` | The pages (sign-in, account, upload, measurements, try-on), the top bar (`nav.js`), Firebase start-up shared by the sign-in pages (`auth-client.js`) and the height picker |

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

### Sign-in fails with `403`, `PERMISSION_DENIED` or "requires a quota project"

The server checks sign-ins with your Google Cloud login. Run `gcloud auth application-default set-quota-project YOUR_PROJECT_ID`, and check you have **Firebase Authentication Admin** (see [Access](#access)). Restart the server afterwards.

### "Sign-in is unavailable right now" after choosing an account

The server couldn't save the user record in Firestore. Its log says why, after `couldn't save the user record`:
- **`Cloud Firestore API has not been used` or `SERVICE_DISABLED`**, or **`database (default) does not exist`**: the database hasn't been created. See step 6 of [Sign-in](#sign-in).
- **`403` / `PERMISSION_DENIED`**: you're missing **Cloud Datastore User** (see [Access](#access)).

### Signed in, but sent straight back to the sign-in page

The session cookie isn't being kept. On `http://localhost` in Safari, set `COOKIE_SECURE=false` in `.env`. Elsewhere, open the app as `http://localhost:8000`, not by a LAN address such as `http://192.168.x.x:8000`: over plain HTTP, browsers only keep `Secure` cookies for `localhost`.

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
