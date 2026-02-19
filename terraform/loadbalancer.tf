# ─── Regional External Application Load Balancer ─────────────────────────────
# Scheme: EXTERNAL_MANAGED, Standard tier, asia-southeast1
# Backend: Serverless NEG → ofac-screening-api (Cloud Run)

# Proxy-only subnet required for EXTERNAL_MANAGED regional LBs (Envoy proxies)
resource "google_compute_subnetwork" "proxy_only" {
  name          = "ofac-api-proxy-subnet"
  region        = var.region
  project       = var.project_id
  network       = "remote-machine"
  ip_cidr_range = "10.100.0.0/24"
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# Static regional external IP
resource "google_compute_address" "lb_ip" {
  name         = "ofac-api-lb-ip"
  region       = var.region
  project      = var.project_id
  address_type = "EXTERNAL"
  network_tier = "STANDARD"

  depends_on = [google_project_service.apis]
}

# Serverless NEG pointing to the Cloud Run service
resource "google_compute_region_network_endpoint_group" "api_neg" {
  name                  = "ofac-api-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region
  project               = var.project_id

  cloud_run {
    service = google_cloud_run_v2_service.ofac_api.name
  }
}

# Backend service (HTTPS to Cloud Run)
resource "google_compute_region_backend_service" "api_backend" {
  name                  = "ofac-api-backend"
  region                = var.region
  project               = var.project_id
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group           = google_compute_region_network_endpoint_group.api_neg.id
    capacity_scaler = 1.0
  }
}

# HTTPS URL map
resource "google_compute_region_url_map" "https" {
  name            = "ofac-api-url-map"
  region          = var.region
  project         = var.project_id
  default_service = google_compute_region_backend_service.api_backend.id
}

# HTTPS target proxy (attaches Certificate Manager cert directly)
resource "google_compute_region_target_https_proxy" "https_proxy" {
  name    = "ofac-api-https-proxy"
  region  = var.region
  project = var.project_id
  url_map = google_compute_region_url_map.https.id

  certificate_manager_certificates = [
    "//certificatemanager.googleapis.com/${google_certificate_manager_certificate.api_cert.id}"
  ]
}

# HTTPS forwarding rule (port 443)
resource "google_compute_forwarding_rule" "https" {
  name                  = "ofac-api-https-forwarding-rule"
  region                = var.region
  project               = var.project_id
  ip_address            = google_compute_address.lb_ip.address
  ip_protocol           = "TCP"
  port_range            = "443"
  target                = google_compute_region_target_https_proxy.https_proxy.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
  network_tier          = "STANDARD"
  network               = "remote-machine"

  depends_on = [google_compute_subnetwork.proxy_only]
}

# ─── HTTP → HTTPS redirect ────────────────────────────────────────────────────

resource "google_compute_region_url_map" "http_redirect" {
  name    = "ofac-api-http-redirect"
  region  = var.region
  project = var.project_id

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_region_target_http_proxy" "http_proxy" {
  name    = "ofac-api-http-proxy"
  region  = var.region
  project = var.project_id
  url_map = google_compute_region_url_map.http_redirect.id
}

resource "google_compute_forwarding_rule" "http" {
  name                  = "ofac-api-http-forwarding-rule"
  region                = var.region
  project               = var.project_id
  ip_address            = google_compute_address.lb_ip.address
  ip_protocol           = "TCP"
  port_range            = "80"
  target                = google_compute_region_target_http_proxy.http_proxy.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
  network_tier          = "STANDARD"
  network               = "remote-machine"

  depends_on = [google_compute_subnetwork.proxy_only]
}
