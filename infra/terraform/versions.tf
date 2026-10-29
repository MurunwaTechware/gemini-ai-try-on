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

  # State lives in its own Cloud Storage bucket, so everyone running Terraform shares one copy (with
  # locking, and versioning to roll back a bad one). The bucket was made by hand, once, outside Terraform
  # (see README); it's separate from the results bucket, which the app's accounts can read and write.
  # Variables can't be used here, so the name is written out.
  backend "gcs" {
    bucket = "project-d045636a-95f8-4566-932-tfstate"
    prefix = "tryon"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}
