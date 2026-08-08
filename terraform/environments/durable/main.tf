locals {
  name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "Terraform"
    Repository  = "j2d3/eth-validator-platform"
    DataTier    = "durable"
  }
}

# These containers deliberately live in a separate Terraform state from the
# EKS/VPC/RDS foundation. A cold-standby destroy of the ephemeral root cannot
# include them in its graph. Values are written by restricted onboarding tools,
# never by Terraform.
resource "aws_secretsmanager_secret" "engine_jwt" {
  name                    = "${local.name}/ethereum/engine-jwt"
  description             = "EL/CL Engine API JWT; value is injected outside Terraform."
  recovery_window_in_days = 7

  lifecycle {
    prevent_destroy = true
  }

  tags = { DataClassification = "engine-jwt" }
}

resource "aws_secretsmanager_secret" "web3signer_database" {
  name                    = "${local.name}/signing/web3signer-database"
  description             = "Web3Signer application database connection JSON; populated outside Terraform."
  recovery_window_in_days = 7

  lifecycle {
    prevent_destroy = true
  }

  tags = { DataClassification = "database-credential" }
}

resource "aws_secretsmanager_secret" "web3signer_signing_key" {
  for_each = var.validator_signing_secret_names

  name                    = "${local.name}/signing/${each.value}"
  description             = "Encrypted validator keystore bundle and password; populated outside Terraform."
  recovery_window_in_days = 30

  lifecycle {
    prevent_destroy = true
  }

  tags = { DataClassification = "validator-signing-key" }
}
