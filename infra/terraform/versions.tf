terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0" # the exact version is pinned in .terraform.lock.hcl on first `terraform init`
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
