terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0" # the exact version is pinned in .terraform.lock.hcl on first `terraform init`
    }
    # Only for google_project_service_identity (pubsub.tf), which isn't in the main provider yet
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.0"
    }
  }

  # State is local for now (infra/terraform/terraform.tfstate, git-ignored). Before more than one
  # person runs Terraform, move it to a Cloud Storage bucket so everyone shares the same state:
  # backend "gcs" {
  #   bucket = "<state bucket name>"
  #   prefix = "tryon"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}
