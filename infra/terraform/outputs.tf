# Values for the app's settings, deploying, and CI

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

output "dead_letter_subscription" {
  description = "Where jobs that failed every attempt end up, for inspection."
  value       = google_pubsub_subscription.measure_jobs_dead_letter.id
}

output "products_sql_connection_name" {
  description = "Connection name of the products database (project:region:instance), for the Cloud SQL proxy or connector."
  value       = data.google_sql_database_instance.products.connection_name
}

# ── Step 2 ──

output "image_repository" {
  description = "Where to push images: <this>/backend:<tag>, <this>/products:<tag>, <this>/frontend:<tag>."
  value       = local.image_repo
}

output "app_url" {
  description = "The app's address (the frontend). Add its host to Firebase's Authorized domains."
  value       = local.frontend_url
}

output "web_url" {
  description = "The try-on backend's own address (the frontend forwards to it)."
  value       = var.deploy_services ? google_cloud_run_v2_service.web[0].uri : null
}

output "products_url" {
  description = "The products API's own address."
  value       = var.deploy_services ? google_cloud_run_v2_service.products[0].uri : null
}

output "deployer_service_account" {
  description = "For GitHub Actions (google-github-actions/auth: service_account)."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "For GitHub Actions (google-github-actions/auth: workload_identity_provider)."
  value       = google_iam_workload_identity_pool_provider.github.name
}
