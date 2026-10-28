# The deployment on Google Cloud:
#   step 1: storage, messaging, Firestore (storage.tf, pubsub.tf, firestore.tf, monitoring.tf)
#   step 2: images, service accounts, the products database secret, CI identity, and the Cloud Run
#           services (registry.tf, service_accounts.tf, secrets.tf, cicd.tf, cloudrun.tf)
# How to deploy, in order: the "Deploying to Cloud Run" section of the project's README.
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
    # Step 2
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",  # GitHub Actions swapping its token for the deployer's
    "sts.googleapis.com",             # (Workload Identity Federation)
    "sqladmin.googleapis.com",        # Cloud Run's connection to the products database
    "aiplatform.googleapis.com",      # Gemini
    "identitytoolkit.googleapis.com", # Firebase Authentication
  ])
  project = var.project_id
  service = each.value

  # Leave the API on if this config is destroyed; other things in the project may use it
  disable_on_destroy = false
}
