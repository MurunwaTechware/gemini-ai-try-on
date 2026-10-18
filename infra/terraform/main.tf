# Step 1 of the deployment: storage and messaging. Service accounts and Cloud Run come next.
#
#   cp terraform.example.tfvars terraform.tfvars   # then fill in the values
#   terraform init
#   terraform plan    # review before applying
#   terraform apply

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "apis" {
  for_each = toset([
    "storage.googleapis.com",
    "pubsub.googleapis.com",
    "monitoring.googleapis.com",
    "firestore.googleapis.com",
  ])
  project = var.project_id
  service = each.value

  # Leave the API on if this config is destroyed; other things in the project may use it
  disable_on_destroy = false
}
