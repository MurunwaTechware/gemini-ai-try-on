variable "project_id" {
  description = "Google Cloud project to deploy into (the one whose billing account holds the credits)."
  type        = string
}

variable "region" {
  description = <<-EOT
    Region for the results bucket (and, later, Cloud Run). The bucket holds photos of people and
    their measurements, so pick with data protection in mind: e.g. africa-south1 (Johannesburg)
    keeps South African users' data in the country. Cloud Storage's always-free allowance only
    covers us-central1, us-east1 and us-west1.
  EOT
  type        = string
}

variable "name_prefix" {
  description = "Prefix for resource names, so several environments can share a project."
  type        = string
  default     = "tryon"
}

variable "results_retention_days" {
  description = "Delete uploads and results this many days after they were created. 0 keeps them forever."
  type        = number
  default     = 0
}

variable "max_delivery_attempts" {
  description = "How many times a measurement job is tried before it goes to the dead-letter topic (5-100)."
  type        = number
  default     = 5
}

variable "alert_email" {
  description = "Email that gets alerts (e.g. jobs in the dead-letter queue). Empty: alerts only show in the console."
  type        = string
  default     = ""
}

variable "products_sql_instance" {
  description = "Name of the existing Cloud SQL instance holding the products database. Only looked up, never managed."
  type        = string
  default     = "post-1"
}

# ── Step 2: Cloud Run ─────────────────────────────────────────────────────────

variable "deploy_services" {
  description = "Create the Cloud Run services and worker pool. Leave false until the first images are pushed (README, Deploying to Cloud Run)."
  type        = bool
  default     = false
}

variable "backend_image" {
  description = "First backend image (web server and worker), e.g. <region>-docker.pkg.dev/<project>/tryon/backend:v1. Later versions are deployed by CI."
  type        = string
  default     = ""
}

variable "products_image" {
  description = "First products API image, e.g. <region>-docker.pkg.dev/<project>/tryon/products:v1."
  type        = string
  default     = ""
}

variable "frontend_image" {
  description = "First frontend image, e.g. <region>-docker.pkg.dev/<project>/tryon/frontend:v1."
  type        = string
  default     = ""
}

variable "firebase_api_key" {
  description = "Firebase web API key (FIREBASE_API_KEY in .env). Public by design: it only identifies the project."
  type        = string
  default     = ""
}

variable "allowed_emails" {
  description = "Who may sign in: addresses and whole @domains, comma-separated (ALLOWED_EMAILS). Required to deploy."
  type        = string
  default     = ""
}

variable "web_max_instances" {
  description = "Most copies of the web server and of the frontend Cloud Run may start."
  type        = number
  default     = 3
}

variable "worker_instances" {
  description = "Measurement workers always running. 0 stops them."
  type        = number
  default     = 1
}

variable "worker_concurrency" {
  description = "Measurements each worker runs at once. Keep worker_instances x this within the Gemini quota."
  type        = number
  default     = 4
}

variable "github_owner" {
  description = "GitHub account or organisation whose repositories may deploy (GitHub Actions)."
  type        = string
  default     = "MurunwaTechware"
}

variable "github_repositories" {
  description = "Repositories (without the owner) whose GitHub Actions workflows may deploy."
  type        = list(string)
  default     = ["gemini-ai-try-on", "lovable-demo-frontend"]
}
