output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API endpoint."
  value       = module.eks.cluster_endpoint
}

output "aws_region" {
  description = "Deployment region."
  value       = var.aws_region
}

output "engine_jwt_secret_arn" {
  description = "Secrets Manager ARN populated by the deployment workflow after apply."
  value       = aws_secretsmanager_secret.engine_jwt.arn
}

output "external_secrets_role_arn" {
  description = "Pod Identity role used by External Secrets Operator."
  value       = aws_iam_role.external_secrets.arn
}

output "configure_kubectl" {
  description = "Command for an authenticated operator."
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}
