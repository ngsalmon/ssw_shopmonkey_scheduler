terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "shopmonkey-scheduler-terraform-state"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable required APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "compute.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "orgpolicy.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# Artifact Registry repository
resource "google_artifact_registry_repository" "scheduler" {
  location      = "us"
  repository_id = "shopmonkey-scheduler"
  format        = "DOCKER"
  description   = "Shopmonkey Scheduler Docker images"

  depends_on = [google_project_service.apis]
}

# Secret Manager secrets
resource "google_secret_manager_secret" "shopmonkey_api_token" {
  secret_id = "SHOPMONKEY_API_TOKEN"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "google_sheets_id" {
  secret_id = "GOOGLE_SHEETS_ID"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "google_credentials_json" {
  secret_id = "GOOGLE_CREDENTIALS_JSON"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "smtp_password" {
  secret_id = "SMTP_PASSWORD"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Cloud Run service
resource "google_cloud_run_v2_service" "scheduler" {
  name     = "shopmonkey-scheduler"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    containers {
      image = "us-docker.pkg.dev/${var.project_id}/shopmonkey-scheduler/shopmonkey-scheduler:latest"

      env {
        name  = "SHOPMONKEY_API_BASE_URL"
        value = "https://api.shopmonkey.cloud"
      }

      env {
        name  = "GOOGLE_APPLICATION_CREDENTIALS"
        value = "/secrets/google-credentials.json"
      }

      env {
        name = "SHOPMONKEY_API_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.shopmonkey_api_token.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "GOOGLE_SHEETS_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_sheets_id.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "ALLOWED_ORIGINS"
        value = "https://scheduler.salmonspeedworx.com"
      }

      env {
        name  = "SMTP_HOST"
        value = "smtp.gmail.com"
      }

      env {
        name  = "SMTP_PORT"
        value = "587"
      }

      env {
        name  = "SMTP_USER"
        value = var.smtp_user
      }

      env {
        name  = "SMTP_USE_TLS"
        value = "true"
      }

      env {
        name  = "EMAIL_FROM"
        value = var.email_from
      }

      env {
        name  = "NOTIFICATION_EMAIL"
        value = var.notification_email
      }

      env {
        name = "SMTP_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.smtp_password.secret_id
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "google-credentials"
        mount_path = "/secrets"
      }

      ports {
        container_port = 8080
      }
    }

    volumes {
      name = "google-credentials"
      secret {
        secret = google_secret_manager_secret.google_credentials_json.secret_id
        items {
          version = "latest"
          path    = "google-credentials.json"
        }
      }
    }

    service_account = google_service_account.cloud_run.email
  }

  depends_on = [google_project_service.apis]
}

# Cloud Run service account
resource "google_service_account" "cloud_run" {
  account_id   = "cloud-run-scheduler"
  display_name = "Cloud Run Scheduler Service Account"
}

# Grant secret access to Cloud Run service account
resource "google_secret_manager_secret_iam_member" "shopmonkey_token_access" {
  secret_id = google_secret_manager_secret.shopmonkey_api_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_secret_manager_secret_iam_member" "sheets_id_access" {
  secret_id = google_secret_manager_secret.google_sheets_id.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_secret_manager_secret_iam_member" "credentials_access" {
  secret_id = google_secret_manager_secret.google_credentials_json.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_secret_manager_secret_iam_member" "smtp_password_access" {
  secret_id = google_secret_manager_secret.smtp_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

# Allow public access to Cloud Run
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  name     = google_cloud_run_v2_service.scheduler.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Org policy to allow allUsers (required for public Cloud Run)
resource "google_org_policy_policy" "allow_all_users" {
  name   = "projects/${var.project_id}/policies/iam.allowedPolicyMemberDomains"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      allow_all = "TRUE"
    }
  }
}
