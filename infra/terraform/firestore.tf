# User records (user_store.py): one document per user, users/<UID>.
# Job status records (job_status.py): one document per measurement job, measurement_jobs/<job ID>.
#
# A project has one "(default)" database, and its location can't be changed after it's created.
# If it was already created by hand (README, Sign-in), bring it under Terraform instead of creating it:
#   terraform import google_firestore_database.default "projects/<project ID>/databases/(default)"

resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region # same place as the photos, for the same data-protection reasons
  type        = "FIRESTORE_NATIVE"

  # Only the server reads it (Admin SDK); the default security rules refuse browsers, which is what we want.
  # Never delete user records on `terraform destroy`; remove the database by hand if that's really meant.
  deletion_policy = "ABANDON"

  # Its location can't change, so a different var.region would make Terraform plan to replace it (and a
  # project's (default) database can't be recreated). Refuse instead: set region to the database's location.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# Job status records are only useful while someone waits for a measurement. Firestore deletes each one
# once its expires_at has passed (job_status.KEEP_FOR after its last change), usually within a day.
resource "google_firestore_field" "measurement_jobs_ttl" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "measurement_jobs"
  field      = "expires_at"

  ttl_config {}

  # Nothing queries by expiry, so don't index it (saves a little on every write)
  index_config {}
}
