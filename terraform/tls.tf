# ─── Certificate Manager — Regional TLS for sanctions.krozario.demo.altostrat.com ─────
# Regional managed certificate attached directly to the regional HTTPS proxy
# (no cert map needed for single-domain / single-cert scenarios)

# DNS authorization (regional)
resource "google_certificate_manager_dns_authorization" "api_dns_auth" {
  name     = "ofac-api-dns-auth"
  project  = var.project_id
  location = var.region
  domain   = "${var.subdomain}.${var.base_domain}"

  depends_on = [google_project_service.apis]
}

# CNAME record in dns-krozario project to satisfy the DNS authorization challenge
resource "google_dns_record_set" "dns_auth_cname" {
  provider     = google.dns
  project      = var.dns_project_id
  managed_zone = var.dns_zone_name
  name         = google_certificate_manager_dns_authorization.api_dns_auth.dns_resource_record[0].name
  type         = google_certificate_manager_dns_authorization.api_dns_auth.dns_resource_record[0].type
  ttl          = 300
  rrdatas      = [google_certificate_manager_dns_authorization.api_dns_auth.dns_resource_record[0].data]
}

# Regional managed certificate
resource "google_certificate_manager_certificate" "api_cert" {
  name     = "ofac-api-cert"
  project  = var.project_id
  location = var.region

  managed {
    domains            = ["${var.subdomain}.${var.base_domain}"]
    dns_authorizations = [google_certificate_manager_dns_authorization.api_dns_auth.id]
  }

  depends_on = [google_dns_record_set.dns_auth_cname]
}

# ─── DNS Records in dns-krozario project ──────────────────────────────────────

# A record: sanctions.krozario.demo.altostrat.com → LB IP
resource "google_dns_record_set" "api_a_record" {
  provider     = google.dns
  project      = var.dns_project_id
  managed_zone = var.dns_zone_name
  name         = "${var.subdomain}.${var.base_domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.lb_ip.address]
}
