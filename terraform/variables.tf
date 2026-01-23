variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "shopmonkey-scheduler"
}

variable "region" {
  description = "GCP region for Cloud Run"
  type        = string
  default     = "us-central1"
}

variable "github_repo" {
  description = "GitHub repository in format owner/repo"
  type        = string
  default     = "ngsalmon/ssw_shopmonkey_scheduler"
}

variable "domains" {
  description = "Custom domains for the scheduler"
  type        = list(string)
  default = [
    "scheduler.salmonspeedworx.com",
    "api.salmonspeedworx.com"
  ]
}

variable "smtp_user" {
  description = "SMTP username for email notifications"
  type        = string
  default     = "nathan@salmonspeedworx.com"
}

variable "email_from" {
  description = "Email address to send notifications from"
  type        = string
  default     = "sales@salmonspeedworx.com"
}

variable "notification_email" {
  description = "Email address to send booking notifications to"
  type        = string
  default     = "sales@salmonspeedworx.com"
}
