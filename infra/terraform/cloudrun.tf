# The app on Cloud Run:
#   frontend   the React app's Node server; forwards API calls to web and products (one address for the browser)
#   web        the try-on backend (uploads, progress stream, try-on, sign-in)
#   products   the Superbalist products API, connected to the existing Cloud SQL instance (cloudsql.tf)
#   worker     a worker pool: pulls measurement jobs from Pub/Sub, a fixed number of instances
#
# Created once the first images are pushed (deploy_services = true, see the README). After that, GitHub
# Actions (or gcloud) deploys new image versions, so Terraform ignores which image is running; any other
# change here (settings, environment) still applies on the next `terraform apply`.

locals {
  services = var.deploy_services ? 1 : 0

  # Cloud Run's fixed address for a service is https://<name>-<project number>.<region>.run.app, known before
  # the service exists. The web server needs the frontend's (TRUSTED_ORIGINS) and the frontend needs the web
  # server's, so this breaks the loop. Open the app at this address; sign-in is set up for it.
  frontend_name = "${var.name_prefix}-frontend"
  frontend_url  = "https://${local.frontend_name}-${data.google_project.this.number}.${var.region}.run.app"

  # Settings shared by the web server and the worker (config.py)
  backend_env = {
    GOOGLE_CLOUD_PROJECT  = var.project_id
    GOOGLE_CLOUD_LOCATION = "global"
    QUEUE_BACKEND         = "pubsub"
    STORAGE_BACKEND       = "gcs"
    RESULTS_BUCKET        = google_storage_bucket.results.name
  }
}

# ── Web server ────────────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "web" {
  count               = local.services
  project             = var.project_id
  name                = "${var.name_prefix}-web"
  location            = var.region
  deletion_protection = false # nothing is stored in the service itself; data is in the bucket and Firestore
  ingress             = "INGRESS_TRAFFIC_ALL"
  # Reachable without a Google login: the app does its own sign-in (ALLOWED_EMAILS), and the frontend's
  # server forwards browsers' requests here
  invoker_iam_disabled = true

  template {
    service_account = google_service_account.web.email
    # The progress stream stays open for up to 5 minutes, and a try-on can take a minute
    timeout = "900s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.web_max_instances
    }

    containers {
      image = var.backend_image
      ports {
        container_port = 8000
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      dynamic "env" {
        for_each = merge(local.backend_env, {
          PUBSUB_TOPIC     = google_pubsub_topic.measure_jobs.id
          AUTH_ENABLED     = "true"
          FIREBASE_API_KEY = var.firebase_api_key
          ALLOWED_EMAILS   = var.allowed_emails
          TRUSTED_ORIGINS  = local.frontend_url
        })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
    precondition {
      condition     = var.backend_image != ""
      error_message = "Set backend_image to the pushed backend image (see the README) before deploy_services = true."
    }
    precondition {
      condition     = var.allowed_emails != "" && var.firebase_api_key != ""
      error_message = "Set allowed_emails (who may sign in) and firebase_api_key: without allowed_emails, anyone with a Google account could use the app."
    }
  }

  depends_on = [
    google_project_iam_member.web,
    google_storage_bucket_iam_member.web_results,
    google_pubsub_topic_iam_member.web_publishes_jobs,
  ]
}

# ── Worker ────────────────────────────────────────────────────────────────────

# Pulls jobs from Pub/Sub and runs them. A worker pool has no address and runs a fixed number of instances,
# so at most worker_instances x worker_concurrency measurements reach Gemini at once (its quota).
resource "google_cloud_run_v2_worker_pool" "worker" {
  count               = local.services
  project             = var.project_id
  name                = "${var.name_prefix}-worker"
  location            = var.region
  deletion_protection = false

  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = var.worker_instances # 0 stops the worker
  }

  template {
    service_account = google_service_account.worker.email

    containers {
      image   = var.backend_image # the same image as the web server, started as the worker
      command = ["python", "worker.py"]
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      dynamic "env" {
        for_each = merge(local.backend_env, {
          PUBSUB_SUBSCRIPTION = google_pubsub_subscription.measure_jobs_workers.id
          WORKER_CONCURRENCY  = tostring(var.worker_concurrency)
        })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [
    google_project_iam_member.worker,
    google_storage_bucket_iam_member.worker_results,
    google_pubsub_subscription_iam_member.worker_pulls_jobs,
  ]
}

# ── Products API ──────────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "products" {
  count                = local.services
  project              = var.project_id
  name                 = "${var.name_prefix}-products"
  location             = var.region
  deletion_protection  = false
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true # the catalogue is public data; the frontend's server calls it

  template {
    service_account = google_service_account.products.email

    scaling {
      min_instance_count = 0
      max_instance_count = 3 # x DB_POOL_SIZE (8) connections stays within the database's 50
    }

    # Cloud Run's own connection to the database: a socket under /cloudsql, no proxy container needed
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [data.google_sql_database_instance.products.connection_name]
      }
    }

    containers {
      image = var.products_image
      ports {
        container_port = 8001
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # DATABASE_URL (with the password) comes from Secret Manager; DATABASE_HOST points it at the socket
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.products_database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "DATABASE_HOST"
        value = "/cloudsql/${data.google_sql_database_instance.products.connection_name}"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
    precondition {
      condition     = var.products_image != ""
      error_message = "Set products_image to the pushed products API image (see the README) before deploy_services = true."
    }
  }

  depends_on = [
    google_project_iam_member.products_sql,
    google_secret_manager_secret_iam_member.products_reads_database_url,
  ]
}

# ── Frontend ──────────────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "frontend" {
  count                = local.services
  project              = var.project_id
  name                 = local.frontend_name
  location             = var.region
  deletion_protection  = false
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true # the public website

  template {
    service_account = google_service_account.frontend.email
    timeout         = "900s" # it forwards the progress stream, which stays open for up to 5 minutes

    scaling {
      min_instance_count = 0
      max_instance_count = var.web_max_instances
    }

    containers {
      image = var.frontend_image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      env {
        name  = "BACKEND_URL"
        value = google_cloud_run_v2_service.web[0].uri
      }
      env {
        name  = "PRODUCTS_API_URL"
        value = google_cloud_run_v2_service.products[0].uri
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
    precondition {
      condition     = var.frontend_image != ""
      error_message = "Set frontend_image to the pushed frontend image (see the README) before deploy_services = true."
    }
  }
}
