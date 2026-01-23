output "cloud_run_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.scheduler.uri
}

output "load_balancer_ip" {
  description = "Load Balancer static IP address"
  value       = google_compute_global_address.scheduler.address
}

output "scheduler_url" {
  description = "Scheduler UI URL"
  value       = "https://scheduler.salmonspeedworx.com"
}

output "api_url" {
  description = "API URL"
  value       = "https://api.salmonspeedworx.com/scheduler"
}
