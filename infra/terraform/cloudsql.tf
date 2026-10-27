# The products database (Superbalist catalogue, read by superbalist/products_api.py). It was set up by
# hand and isn't managed here: this only looks it up, so Terraform can never change or delete it.
# It's referenced for what deploying needs, e.g. the connection name the Cloud SQL connector on Cloud Run
# uses. To manage it from Terraform instead, it would have to be imported first (and protected with
# deletion_protection and prevent_destroy).

data "google_sql_database_instance" "products" {
  name    = var.products_sql_instance
  project = var.project_id
}
