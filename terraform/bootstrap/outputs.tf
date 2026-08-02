output "state_bucket" {
  description = "S3 bucket to configure in the dev backend."
  value       = aws_s3_bucket.terraform_state.id
}

output "backend_config" {
  description = "Backend configuration values for GitHub repository variables."
  value = {
    region       = var.aws_region
    bucket       = aws_s3_bucket.terraform_state.id
    key          = "environments/dev/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
