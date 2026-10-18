# User records (user_store.py): one document per user, users/<UID>.
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

  depends_on = [google_project_service.apis]
}
