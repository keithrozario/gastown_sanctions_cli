resource "google_artifact_registry_repository" "ofac" {
  location      = var.region
  repository_id = var.artifact_repo_name
  description   = "Docker images for OFAC SDN Dataflow pipeline"
  format        = "DOCKER"
  project       = var.project_id

  depends_on = [google_project_service.apis]
}
