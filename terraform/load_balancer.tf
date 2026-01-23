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

# Managed SSL certificate
resource "google_compute_managed_ssl_certificate" "scheduler" {
  name = "scheduler-ssl-cert"

  managed {
    domains = [
      "scheduler.salmonspeedworx.com",
      "api.salmonspeedworx.com"
    ]
  }
}

# HTTPS proxy
resource "google_compute_target_https_proxy" "scheduler" {
  name             = "scheduler-https-proxy"
  url_map          = google_compute_url_map.scheduler.id
  ssl_certificates = [google_compute_managed_ssl_certificate.scheduler.id]
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
