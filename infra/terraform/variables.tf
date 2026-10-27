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
