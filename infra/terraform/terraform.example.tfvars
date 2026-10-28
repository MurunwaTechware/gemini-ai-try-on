# Copy to terraform.tfvars (git-ignored) and fill in.

project_id = "your-project-id"
region     = "africa-south1" # see the region notes in variables.tf

# Optional (defaults shown)
# name_prefix            = "tryon"
# results_retention_days = 0          # 0 keeps files forever; e.g. 30 deletes ALL files (profiles, saved try-ons) after 30 days
# max_delivery_attempts  = 5
# alert_email            = "you@example.com"
# products_sql_instance  = "post-1"   # existing Cloud SQL instance, only looked up

# Step 2, Cloud Run. Leave deploy_services false until the first images are pushed (README, Deploying to Cloud Run)
# deploy_services  = true
# backend_image    = "africa-south1-docker.pkg.dev/your-project-id/tryon/backend:v1"
# products_image   = "africa-south1-docker.pkg.dev/your-project-id/tryon/products:v1"
# frontend_image   = "africa-south1-docker.pkg.dev/your-project-id/tryon/frontend:v1"
# firebase_api_key = ""                     # FIREBASE_API_KEY from .env
# allowed_emails   = "@techware.tech"       # who may sign in; add external people's addresses
# Optional (defaults shown)
# web_max_instances   = 3
# worker_instances    = 1
# worker_concurrency  = 4
# github_owner        = "MurunwaTechware"
# github_repositories = ["gemini-ai-try-on", "lovable-demo-frontend"]
