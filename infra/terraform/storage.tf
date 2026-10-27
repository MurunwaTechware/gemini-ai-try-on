# Replaces the local results/ folder: job photos, inputs.json, measurement.json and try-on output.
# Shared by the web server and workers, which is what lets them run on different machines.

resource "google_storage_bucket" "results" {
  name     = "${var.project_id}-${var.name_prefix}-results" # bucket names are global across Google Cloud
  project  = var.project_id
  location = var.region

  # Photos of real people: never public, and access is managed for the whole bucket, not per file
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Refuse to delete the bucket while it still holds files, so a `terraform destroy` can't wipe data
  force_destroy = false

  # And refuse any plan that would delete or replace it (e.g. after changing var.region)
  lifecycle {
    prevent_destroy = true
  }

  dynamic "lifecycle_rule" {
    for_each = var.results_retention_days > 0 ? [1] : []
    content {
      condition {
        age = var.results_retention_days
      }
      action {
        type = "Delete"
      }
    }
  }

  depends_on = [google_project_service.apis]
}
