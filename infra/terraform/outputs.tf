# Values the app needs once it's changed to use Cloud Storage and Pub/Sub

output "results_bucket" {
  description = "Bucket that replaces the local results/ folder."
  value       = google_storage_bucket.results.name
}

output "measure_jobs_topic" {
  description = "Topic the web server publishes measurement jobs to."
  value       = google_pubsub_topic.measure_jobs.id
}

output "measure_jobs_subscription" {
  description = "Subscription the workers pull jobs from."
  value       = google_pubsub_subscription.measure_jobs_workers.id
}

output "job_events_topic" {
  description = "Topic workers publish 'job done' events to."
  value       = google_pubsub_topic.job_events.id
}

output "dead_letter_subscription" {
  description = "Where jobs that failed every attempt end up, for inspection."
  value       = google_pubsub_subscription.measure_jobs_dead_letter.id
}
