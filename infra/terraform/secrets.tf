# The products database's connection string (it holds the password), read by the products API.
# Terraform creates the secret but never its value, so the password stays out of the Terraform state.
# Add the value once, as the README's "Deploying to Cloud Run" section shows.

resource "google_secret_manager_secret" "products_database_url" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-products-database-url"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "products_reads_database_url" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.products_database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.products.member
}
