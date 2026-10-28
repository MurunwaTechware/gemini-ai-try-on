# GitHub Actions deploying new versions, without a service account key stored in GitHub. Workload Identity
# Federation: a workflow presents GitHub's own short-lived token, and Google swaps it for the deployer's,
# but only for workflows in the repositories listed in var.github_repositories.
#
# The deployer may push images and deploy new revisions of the Cloud Run services and worker pool. It can't
# change their settings' permissions, the bucket, Pub/Sub or anything else: Terraform owns those.

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-deployer"
  display_name = "GitHub Actions deployer"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub Actions OIDC"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }
  # Only tokens from repositories owned by this GitHub account are accepted at all
  attribute_condition = "assertion.repository_owner == '${var.github_owner}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Which repositories may act as the deployer
resource "google_service_account_iam_member" "deployer_from_github" {
  for_each           = toset(var.github_repositories)
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${each.value}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_pushes_images" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deployer.member
}

resource "google_project_iam_member" "deployer_deploys" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = google_service_account.deployer.member
}

# Deploying a revision that runs as a service account requires permission to "act as" that account
resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each = {
    web      = google_service_account.web.name
    worker   = google_service_account.worker.name
    products = google_service_account.products.name
    frontend = google_service_account.frontend.name
  }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}
