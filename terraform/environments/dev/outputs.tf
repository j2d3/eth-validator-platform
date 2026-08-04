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
  description = "Pod Identity base role used by External Secrets Operator. It can assume scoped reader roles but cannot read secrets directly."
  value       = aws_iam_role.external_secrets.arn
}

output "external_secrets_reader_role_arns" {
  description = "Scoped roles referenced by the later AWS SecretStores; no secret values are exposed."
  value = {
    engine   = aws_iam_role.external_secrets_engine_reader.arn
    database = aws_iam_role.external_secrets_database_reader.arn
    signing  = aws_iam_role.external_secrets_signing_reader.arn
  }
}

output "web3signer_secret_arns" {
  description = "Empty Secrets Manager containers populated by restricted bootstrap/onboarding procedures outside Terraform."
  value = {
    database_connection = aws_secretsmanager_secret.web3signer_database.arn
    signing_key_bundles = {
      for validator_id, signing_key in aws_secretsmanager_secret.web3signer_signing_key :
      validator_id => signing_key.arn
    }
  }
}

output "web3signer_database" {
  description = "Non-secret RDS connection and recovery interface consumed by the later EKS adapter and trusted bootstrap."
  value = {
    address            = aws_db_instance.web3signer.address
    port               = aws_db_instance.web3signer.port
    database           = aws_db_instance.web3signer.db_name
    resource_id        = aws_db_instance.web3signer.resource_id
    vpc_cidr           = module.vpc.vpc_cidr_block
    ca_cert_identifier = aws_db_instance.web3signer.ca_cert_identifier
    master_secret_arn  = try(aws_db_instance.web3signer.master_user_secret[0].secret_arn, null)
  }
}

output "web3signer_pod_security_group_id" {
  description = "Security group a later EKS SecurityGroupPolicy must assign only to Web3Signer Pods before RDS can be reached."
  value       = aws_security_group.web3signer_pod.id
}

output "web3signer_migration_pod_security_group_id" {
  description = "Separate security group a later EKS SecurityGroupPolicy must assign only to the Web3Signer schema-migration Job."
  value       = aws_security_group.web3signer_migration_pod.id
}

output "ethereum_node_groups_by_az" {
  description = "Zonal Ethereum managed-node-group names and hard capacity bounds. Query EKS for live desired size; the pinned module intentionally ignores that field after creation."
  value = {
    for az in local.azs : az => {
      name          = split(":", module.eks.eks_managed_node_groups["ethereum-${az}"].node_group_id)[1]
      capacity_type = var.ethereum_capacity_type
      max_size      = var.ethereum_max_size_per_az
    }
  }
}

output "configure_kubectl" {
  description = "Command for an authenticated operator."
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}
