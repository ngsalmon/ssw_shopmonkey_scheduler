# Terraform Infrastructure

This directory contains Terraform configuration for the Shopmonkey Scheduler infrastructure on GCP.

## Prerequisites

1. [Terraform](https://www.terraform.io/downloads) >= 1.0
2. [gcloud CLI](https://cloud.google.com/sdk/docs/install) authenticated
3. GCS bucket for Terraform state (create if not exists):
   ```bash
   gcloud storage buckets create gs://shopmonkey-scheduler-terraform-state --location=us
   ```

## Resources Managed

- **Cloud Run Service**: Shopmonkey Scheduler container
- **Artifact Registry**: Docker image repository
- **Secret Manager**: API tokens and credentials
- **Load Balancer**: HTTPS frontend with custom domains
- **SSL Certificate**: Managed certificates for custom domains
- **Workload Identity**: GitHub Actions authentication

## Usage

### Initialize
```bash
terraform init
```

### Plan changes
```bash
terraform plan
```

### Apply changes
```bash
terraform apply
```

### Import existing resources

If you set up infrastructure manually before using Terraform, import existing resources:

```bash
# Import existing resources (one-time)
terraform import google_cloud_run_v2_service.scheduler projects/shopmonkey-scheduler/locations/us-central1/services/shopmonkey-scheduler
terraform import google_compute_global_address.scheduler projects/shopmonkey-scheduler/global/addresses/scheduler-ip
# ... etc
```

## Custom Domains

After applying Terraform, configure DNS:

| Type | Host | Value |
|------|------|-------|
| A | scheduler | (see `load_balancer_ip` output) |
| A | api | (see `load_balancer_ip` output) |

## Secrets

Secrets are managed in GCP Secret Manager. To update a secret:

```bash
echo -n "new-secret-value" | gcloud secrets versions add SECRET_NAME --data-file=-
```

## GitHub Actions

The workflow automatically deploys on push to master. Required GitHub Secrets:

- `GCP_PROJECT_ID`: shopmonkey-scheduler
- `GCP_WORKLOAD_IDENTITY_PROVIDER`: (see `workload_identity_provider` output)
- `GCP_SERVICE_ACCOUNT`: (see `github_service_account_email` output)
