# Load Balancer configuration for custom domains

# Static IP address
resource "google_compute_global_address" "scheduler" {
  name         = "scheduler-ip"
  ip_version   = "IPV4"
  address_type = "EXTERNAL"
}

# Serverless NEG for Cloud Run
resource "google_compute_region_network_endpoint_group" "scheduler" {
  name                  = "scheduler-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.scheduler.name
  }
}

# Backend service
resource "google_compute_backend_service" "scheduler" {
  name                  = "scheduler-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTP"

  backend {
    group = google_compute_region_network_endpoint_group.scheduler.id
  }
}

# URL map for routing
resource "google_compute_url_map" "scheduler" {
  name            = "scheduler-url-map"
  default_service = google_compute_backend_service.scheduler.id

  host_rule {
    hosts        = ["scheduler.salmonspeedworx.com"]
    path_matcher = "scheduler-ui"
  }

  host_rule {
    hosts        = ["api.salmonspeedworx.com"]
    path_matcher = "scheduler-api"
  }

  path_matcher {
    name            = "scheduler-ui"
    default_service = google_compute_backend_service.scheduler.id
  }

  path_matcher {
    name            = "scheduler-api"
    default_service = google_compute_backend_service.scheduler.id

    # Rewrite /scheduler/* to /* (strip prefix)
    route_rules {
      priority = 1
      match_rules {
        prefix_match = "/scheduler"
      }
      route_action {
        url_rewrite {
          path_prefix_rewrite = "/"
        }
        weighted_backend_services {
          backend_service = google_compute_backend_service.scheduler.id
          weight          = 100
        }
      }
    }
  }
}

# TLS certificate: Certificate Manager with DNS authorization.
#
# scheduler/api DNS is proxied through Cloudflare, so a classic
# google_compute_managed_ssl_certificate can never renew (Google's load-balancer
# validation sees Cloudflare's IPs: FAILED_NOT_VISIBLE). The old
# scheduler-ssl-cert-v2 expired 2026-08-09 for exactly this reason.
# DNS authorization proves ownership with an _acme-challenge CNAME instead, so
# the cert renews while proxied. The CNAMEs live in Cloudflare (DNS-only):
#   _acme-challenge.scheduler.salmonspeedworx.com -> <scheduler-dns-auth record>
#   _acme-challenge.api.salmonspeedworx.com       -> <api-dns-auth record>
# (`gcloud certificate-manager dns-authorizations list --project shopmonkey-scheduler`)
#
# NOTE: created with gcloud on 2026-11-12. The GCS state bucket in main.tf does
# not exist; this infrastructure is not under Terraform state. Import before
# any apply.
resource "google_project_service" "certificatemanager" {
  service = "certificatemanager.googleapis.com"
}

resource "google_certificate_manager_dns_authorization" "scheduler" {
  name   = "scheduler-dns-auth"
  domain = "scheduler.salmonspeedworx.com"
}

resource "google_certificate_manager_dns_authorization" "api" {
  name   = "api-dns-auth"
  domain = "api.salmonspeedworx.com"
}

resource "google_certificate_manager_certificate" "scheduler" {
  name = "scheduler-api-cert"
  managed {
    domains = [
      "scheduler.salmonspeedworx.com",
      "api.salmonspeedworx.com",
    ]
    dns_authorizations = [
      google_certificate_manager_dns_authorization.scheduler.id,
      google_certificate_manager_dns_authorization.api.id,
    ]
  }
}

resource "google_certificate_manager_certificate_map" "scheduler" {
  name = "scheduler-cert-map"
}

resource "google_certificate_manager_certificate_map_entry" "scheduler" {
  name         = "scheduler-entry"
  map          = google_certificate_manager_certificate_map.scheduler.name
  hostname     = "scheduler.salmonspeedworx.com"
  certificates = [google_certificate_manager_certificate.scheduler.id]
}

resource "google_certificate_manager_certificate_map_entry" "api" {
  name         = "api-entry"
  map          = google_certificate_manager_certificate_map.scheduler.name
  hostname     = "api.salmonspeedworx.com"
  certificates = [google_certificate_manager_certificate.scheduler.id]
}

# HTTPS proxy
resource "google_compute_target_https_proxy" "scheduler" {
  name            = "scheduler-https-proxy"
  url_map         = google_compute_url_map.scheduler.id
  certificate_map = "//certificatemanager.googleapis.com/${google_certificate_manager_certificate_map.scheduler.id}"
}

# HTTPS forwarding rule
resource "google_compute_global_forwarding_rule" "scheduler_https" {
  name                  = "scheduler-https-rule"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.scheduler.id
  ip_protocol           = "TCP"
  port_range            = "443"
  target                = google_compute_target_https_proxy.scheduler.id
}

# HTTP to HTTPS redirect
resource "google_compute_url_map" "scheduler_redirect" {
  name = "scheduler-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "scheduler_redirect" {
  name    = "scheduler-http-proxy"
  url_map = google_compute_url_map.scheduler_redirect.id
}

resource "google_compute_global_forwarding_rule" "scheduler_http" {
  name                  = "scheduler-http-rule"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.scheduler.id
  ip_protocol           = "TCP"
  port_range            = "80"
  target                = google_compute_target_http_proxy.scheduler_redirect.id
}
