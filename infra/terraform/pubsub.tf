# Replaces RabbitMQ for the job queue (job_queue.py): web server -> one worker, as a topic with one
# shared subscription. How the waiting page hears back isn't here: workers update the job's status
# record in Firestore (job_status.py, firestore.tf), and the web server listens to that.

# ── Jobs ──────────────────────────────────────────────────────────────────────

resource "google_pubsub_topic" "measure_jobs" {
  name       = "${var.name_prefix}-measure-jobs"
  project    = var.project_id
  depends_on = [google_project_service.apis]
}

# All workers share this one subscription, so each job goes to exactly one of them
resource "google_pubsub_subscription" "measure_jobs_workers" {
  name    = "${var.name_prefix}-measure-jobs-workers"
  project = var.project_id
  topic   = google_pubsub_topic.measure_jobs.id

  # A measurement takes ~30 s; Pub/Sub redelivers if the worker hasn't acknowledged by then.
  # Client libraries extend this automatically while a job is still running.
  ack_deadline_seconds       = 120
  message_retention_duration = "86400s" # unprocessed jobs are kept for a day

  # Wait before retrying a failed job, backing off up to 10 minutes
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  # A job that keeps failing moves aside instead of retrying forever (RabbitMQ setup had a TODO for this)
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.measure_jobs_dead_letter.id
    max_delivery_attempts = var.max_delivery_attempts
  }

  # Subscriptions are deleted after 31 days without activity by default; this one must never be
  expiration_policy {
    ttl = ""
  }
}

# ── Dead letters ──────────────────────────────────────────────────────────────

resource "google_pubsub_topic" "measure_jobs_dead_letter" {
  name       = "${var.name_prefix}-measure-jobs-dead-letter"
  project    = var.project_id
  depends_on = [google_project_service.apis]
}

# Without a subscription, dead-lettered messages would be dropped; this keeps them to inspect
resource "google_pubsub_subscription" "measure_jobs_dead_letter" {
  name                       = "${var.name_prefix}-measure-jobs-dead-letter"
  project                    = var.project_id
  topic                      = google_pubsub_topic.measure_jobs_dead_letter.id
  message_retention_duration = "604800s" # 7 days, the maximum

  expiration_policy {
    ttl = ""
  }
}

# Pub/Sub's own service account moves messages to the dead-letter topic, so it needs to
# publish there and to acknowledge (remove) them from the original subscription
locals {
  pubsub_service_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.measure_jobs_dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_service_agent
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.measure_jobs_workers.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_service_agent
}
