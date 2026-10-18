# Alerts. Incidents always appear under Monitoring > Alerting in the console; email is optional.

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  project      = var.project_id
  display_name = "${var.name_prefix} alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  depends_on = [google_project_service.apis]
}

# Open while the dead-letter subscription holds any messages, so it stays visible until someone
# has looked, rather than firing once per job. Pub/Sub reports this metric about once a minute,
# so expect the alert a few minutes after a job is dead-lettered.
resource "google_monitoring_alert_policy" "dead_letter_jobs" {
  project      = var.project_id
  display_name = "${var.name_prefix}: measurement jobs in the dead-letter queue"
  combiner     = "OR"

  conditions {
    display_name = "Dead-letter subscription has messages"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"pubsub_subscription\"",
        "resource.labels.subscription_id = \"${google_pubsub_subscription.measure_jobs_dead_letter.name}\"",
        "metric.type = \"pubsub.googleapis.com/subscription/num_undelivered_messages\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
      trigger {
        count = 1
      }
    }
  }

  notification_channels = google_monitoring_notification_channel.email[*].id

  alert_strategy {
    auto_close = "604800s" # matches the 7-day retention: by then the messages are gone anyway
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      A measurement job failed every attempt and was moved to the dead-letter queue. Users with
      these jobs saw "taking longer than expected" rather than a result.

      Model errors (safety blocks, 403, 404) don't end up here: they fail straight away. Jobs land
      here when the worker crashed or was stopped mid-job on every attempt.

      1. Pull the messages (each is a job ID) from subscription
         `${google_pubsub_subscription.measure_jobs_dead_letter.name}`:
         `gcloud pubsub subscriptions pull ${google_pubsub_subscription.measure_jobs_dead_letter.name} --limit=10`
         Add `--auto-ack` to remove them once noted; the alert closes when none are left.
      2. Find the worker's logs for those job IDs in Cloud Logging.
      3. The job's photos and inputs are in bucket `${google_storage_bucket.results.name}` under the job ID.
    EOT
  }

  depends_on = [google_project_service.apis]
}
