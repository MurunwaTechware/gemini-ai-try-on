# Where the container images live: one Docker repository for the backend (web server and worker share an
# image), the products API and the frontend. Built and pushed by hand at first, then by GitHub Actions.
#   <region>-docker.pkg.dev/<project>/<name_prefix>/backend:<tag>

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.name_prefix
  format        = "DOCKER"
  description   = "Container images for the try-on app"

  # Keep the 10 newest versions of each image, so old builds don't pile up (and cost storage)
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days; the KEEP rule above protects the 10 newest
    }
  }

  depends_on = [google_project_service.apis]
}

locals {
  image_repo = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
