# One service account per Cloud Run service, each allowed only what that service does. Cloud Run gives a
# service its account's credentials automatically, so no login file is needed in the cloud.
# (Without these, services would run as the default Compute account, which can change anything in the project.)

resource "google_service_account" "web" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-web"
  display_name = "Try-on web server (Cloud Run)"
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-worker"
  display_name = "Measurement worker (Cloud Run worker pool)"
}

resource "google_service_account" "products" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-products"
  display_name = "Products API (Cloud Run)"
}

resource "google_service_account" "frontend" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-frontend"
  display_name = "Frontend (Cloud Run). Calls no Google APIs, so it has no roles"
}

# ── Web server: Gemini (try-on), sign-in, user and job records, files, publishing jobs ──

resource "google_project_iam_member" "web" {
  for_each = toset([
    "roles/aiplatform.user",       # Gemini, for try-ons
    "roles/firebaseauth.admin",    # check sign-ins and create session cookies
    "roles/datastore.user",        # user records and job status records in Firestore
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.web.member
}

resource "google_storage_bucket_iam_member" "web_results" {
  bucket = google_storage_bucket.results.name
  role   = "roles/storage.objectUser"
  member = google_service_account.web.member
}

resource "google_pubsub_topic_iam_member" "web_publishes_jobs" {
  project = var.project_id
  topic   = google_pubsub_topic.measure_jobs.name
  role    = "roles/pubsub.publisher"
  member  = google_service_account.web.member
}

# ── Worker: Gemini (measuring), job status records, files, pulling jobs ──

resource "google_project_iam_member" "worker" {
  for_each = toset([
    "roles/aiplatform.user", # Gemini, for measurements
    "roles/datastore.user",  # job status records in Firestore
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.worker.member
}

resource "google_storage_bucket_iam_member" "worker_results" {
  bucket = google_storage_bucket.results.name
  role   = "roles/storage.objectUser"
  member = google_service_account.worker.member
}

resource "google_pubsub_subscription_iam_member" "worker_pulls_jobs" {
  project      = var.project_id
  subscription = google_pubsub_subscription.measure_jobs_workers.name
  role         = "roles/pubsub.subscriber"
  member       = google_service_account.worker.member
}

# ── Products API: the database connection and its secret (secrets.tf) ──

resource "google_project_iam_member" "products_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = google_service_account.products.member
}
