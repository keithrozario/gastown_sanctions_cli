terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.45, < 7.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Alias for the separate Cloud DNS project (dns-krozario)
provider "google" {
  alias   = "dns"
  project = var.dns_project_id
  region  = var.region
}
