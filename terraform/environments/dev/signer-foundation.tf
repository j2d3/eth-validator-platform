locals {
  web3signer_database_identifier = "${local.name}-web3signer"
  web3signer_database_name       = "web3signer"
  web3signer_database_port       = 5432
}

# This group is attached later to Web3Signer's branch ENI by a reviewed EKS
# SecurityGroupPolicy. It is deliberately not a node security group: allowing
# the node group would make every colocated Pod an RDS network principal.
resource "aws_security_group" "web3signer_pod" {
  name_prefix            = "${local.name}-web3signer-pod-"
  description            = "Network identity for Web3Signer Pods; assigned by the EKS overlay."
  vpc_id                 = module.vpc.vpc_id
  revoke_rules_on_delete = true

  tags = {
    Name = "${local.name}-web3signer-pod"
  }
}

# Flyway is a distinct workload in the database namespace. Giving its short-
# lived Job a separate branch-ENI identity prevents schema migration authority
# from inheriting the signer's API/metrics admission surface.
resource "aws_security_group" "web3signer_migration_pod" {
  name_prefix            = "${local.name}-web3signer-migration-pod-"
  description            = "Network identity for the Web3Signer schema-migration Job; assigned by the EKS overlay."
  vpc_id                 = module.vpc.vpc_id
  revoke_rules_on_delete = true

  tags = {
    Name = "${local.name}-web3signer-migration-pod"
  }
}

resource "aws_security_group" "web3signer_database" {
  name_prefix            = "${local.name}-web3signer-db-"
  description            = "Private RDS PostgreSQL boundary for Web3Signer slashing protection."
  vpc_id                 = module.vpc.vpc_id
  revoke_rules_on_delete = true

  tags = {
    Name               = "${local.name}-web3signer-db"
    DataClassification = "slashing-protection"
  }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_web3signer" {
  security_group_id            = aws_security_group.web3signer_database.id
  referenced_security_group_id = aws_security_group.web3signer_pod.id
  description                  = "PostgreSQL from Web3Signer Pod ENIs only"
  from_port                    = local.web3signer_database_port
  to_port                      = local.web3signer_database_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "database_from_web3signer_migration" {
  security_group_id            = aws_security_group.web3signer_database.id
  referenced_security_group_id = aws_security_group.web3signer_migration_pod.id
  description                  = "PostgreSQL from Web3Signer schema-migration Pod ENIs only"
  from_port                    = local.web3signer_database_port
  to_port                      = local.web3signer_database_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web3signer_to_database" {
  security_group_id            = aws_security_group.web3signer_pod.id
  referenced_security_group_id = aws_security_group.web3signer_database.id
  description                  = "Web3Signer slashing-protection PostgreSQL"
  from_port                    = local.web3signer_database_port
  to_port                      = local.web3signer_database_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web3signer_migration_to_database" {
  security_group_id            = aws_security_group.web3signer_migration_pod.id
  referenced_security_group_id = aws_security_group.web3signer_database.id
  description                  = "Flyway schema migration PostgreSQL"
  from_port                    = local.web3signer_database_port
  to_port                      = local.web3signer_database_port
  ip_protocol                  = "tcp"
}

# Web3Signer has TCP health/metrics listeners, but in-cluster source selection
# remains Kubernetes NetworkPolicy's job. These rules make the Pod branch ENI
# compatible with kubelet probes and authorized in-cluster clients/scrapers.
resource "aws_vpc_security_group_ingress_rule" "web3signer_api_from_nodes" {
  security_group_id            = aws_security_group.web3signer_pod.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "In-cluster signer API and kubelet probes"
  from_port                    = 9000
  to_port                      = 9000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "web3signer_metrics_from_nodes" {
  security_group_id            = aws_security_group.web3signer_pod.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "In-cluster Prometheus scrapes"
  from_port                    = 9001
  to_port                      = 9001
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web3signer_dns_udp" {
  security_group_id            = aws_security_group.web3signer_pod.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "Cluster DNS over UDP"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "udp"
}

resource "aws_vpc_security_group_egress_rule" "web3signer_dns_tcp" {
  security_group_id            = aws_security_group.web3signer_pod.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "Cluster DNS over TCP"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web3signer_migration_dns_udp" {
  security_group_id            = aws_security_group.web3signer_migration_pod.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "Cluster DNS for schema migration over UDP"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "udp"
}

resource "aws_vpc_security_group_egress_rule" "web3signer_migration_dns_tcp" {
  security_group_id            = aws_security_group.web3signer_migration_pod.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "Cluster DNS for schema migration over TCP"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "nodes_dns_from_web3signer_udp" {
  security_group_id            = module.eks.node_security_group_id
  referenced_security_group_id = aws_security_group.web3signer_pod.id
  description                  = "Cluster DNS responses for Web3Signer branch ENIs"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "udp"
}

resource "aws_vpc_security_group_ingress_rule" "nodes_dns_from_web3signer_tcp" {
  security_group_id            = module.eks.node_security_group_id
  referenced_security_group_id = aws_security_group.web3signer_pod.id
  description                  = "Cluster DNS TCP for Web3Signer branch ENIs"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "nodes_dns_from_web3signer_migration_udp" {
  security_group_id            = module.eks.node_security_group_id
  referenced_security_group_id = aws_security_group.web3signer_migration_pod.id
  description                  = "Cluster DNS responses for schema-migration branch ENIs"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "udp"
}

resource "aws_vpc_security_group_ingress_rule" "nodes_dns_from_web3signer_migration_tcp" {
  security_group_id            = module.eks.node_security_group_id
  referenced_security_group_id = aws_security_group.web3signer_migration_pod.id
  description                  = "Cluster DNS TCP for schema-migration branch ENIs"
  from_port                    = 53
  to_port                      = 53
  ip_protocol                  = "tcp"
}

resource "aws_db_parameter_group" "web3signer" {
  name_prefix = "${local.name}-web3signer-"
  family      = "postgres${var.rds_postgres_major_version}"
  description = "Web3Signer slashing-protection PostgreSQL parameters"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "web3signer_postgresql" {
  name              = "/aws/rds/instance/${local.web3signer_database_identifier}/postgresql"
  retention_in_days = var.rds_log_retention_days

  tags = {
    DataClassification = "slashing-protection-metadata"
  }
}

resource "aws_db_instance" "web3signer" {
  identifier = local.web3signer_database_identifier

  engine         = "postgres"
  engine_version = var.rds_postgres_major_version
  instance_class = var.rds_instance_class

  db_name  = local.web3signer_database_name
  username = "web3signer_admin"
  port     = local.web3signer_database_port

  # RDS generates and retains the master password in its own Secrets Manager
  # secret. No password expression or secret version enters Terraform state.
  manage_master_user_password = true

  allocated_storage     = var.rds_allocated_storage_gib
  max_allocated_storage = var.rds_max_allocated_storage_gib
  storage_type          = "gp3"
  storage_encrypted     = true

  multi_az               = false
  availability_zone      = local.azs[var.rds_availability_zone_index]
  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.web3signer_database.id]
  publicly_accessible    = false

  parameter_group_name = aws_db_parameter_group.web3signer.name

  backup_retention_period = var.rds_backup_retention_days
  backup_window           = "08:00-08:30"
  maintenance_window      = "sun:09:00-sun:10:00"
  copy_tags_to_snapshot   = true

  deletion_protection       = var.rds_deletion_protection
  skip_final_snapshot       = var.rds_skip_final_snapshot
  final_snapshot_identifier = var.rds_skip_final_snapshot ? null : "${local.web3signer_database_identifier}-final"
  delete_automated_backups  = false

  auto_minor_version_upgrade          = true
  allow_major_version_upgrade         = false
  apply_immediately                   = false
  iam_database_authentication_enabled = false
  performance_insights_enabled        = false
  monitoring_interval                 = 0
  enabled_cloudwatch_logs_exports     = ["postgresql", "upgrade"]

  depends_on = [aws_cloudwatch_log_group.web3signer_postgresql]

  lifecycle {
    precondition {
      condition     = var.rds_max_allocated_storage_gib >= var.rds_allocated_storage_gib
      error_message = "rds_max_allocated_storage_gib must be at least rds_allocated_storage_gib."
    }
  }

  tags = {
    DataClassification = "slashing-protection"
  }
}

# These are containers only. A restricted bootstrap creates the application
# role in PostgreSQL and writes the JSON payload directly to Secrets Manager.
# The canonical source JSON fields are host, port, database, username, and
# password. The EKS adapter may project database to a target dbname key for
# images/configuration that require that spelling; the AWS source stays stable.
# Terraform never declares aws_secretsmanager_secret_version for either object.
resource "aws_secretsmanager_secret" "web3signer_database" {
  name                    = "${local.name}/signing/web3signer-database"
  description             = "Web3Signer application database connection JSON; populated outside Terraform."
  recovery_window_in_days = 7

  tags = {
    DataClassification = "database-credential"
  }
}

resource "aws_secretsmanager_secret" "web3signer_signing_key" {
  name                    = "${local.name}/signing/validator-keystore"
  description             = "Encrypted validator keystore bundle and password; populated by restricted onboarding outside Terraform."
  recovery_window_in_days = 30

  tags = {
    DataClassification = "validator-signing-key"
  }
}

data "aws_iam_policy_document" "external_secrets_reader_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.external_secrets.arn]
    }
  }
}

resource "aws_iam_role" "external_secrets_engine_reader" {
  name               = "${local.name}-eso-engine-reader"
  assume_role_policy = data.aws_iam_policy_document.external_secrets_reader_trust.json
}

data "aws_iam_policy_document" "external_secrets_engine_reader" {
  statement {
    sid       = "ReadEngineJwt"
    effect    = "Allow"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.engine_jwt.arn]
  }
}

resource "aws_iam_role_policy" "external_secrets_engine_reader" {
  name   = "read-engine-jwt"
  role   = aws_iam_role.external_secrets_engine_reader.id
  policy = data.aws_iam_policy_document.external_secrets_engine_reader.json
}

resource "aws_iam_role" "external_secrets_database_reader" {
  name               = "${local.name}-eso-database-reader"
  assume_role_policy = data.aws_iam_policy_document.external_secrets_reader_trust.json
}

data "aws_iam_policy_document" "external_secrets_database_reader" {
  statement {
    sid       = "ReadWeb3SignerDatabaseCredential"
    effect    = "Allow"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.web3signer_database.arn]
  }
}

resource "aws_iam_role_policy" "external_secrets_database_reader" {
  name   = "read-web3signer-database"
  role   = aws_iam_role.external_secrets_database_reader.id
  policy = data.aws_iam_policy_document.external_secrets_database_reader.json
}

resource "aws_iam_role" "external_secrets_signing_reader" {
  name               = "${local.name}-eso-signing-reader"
  assume_role_policy = data.aws_iam_policy_document.external_secrets_reader_trust.json
}

data "aws_iam_policy_document" "external_secrets_signing_reader" {
  statement {
    sid       = "ReadValidatorKeystore"
    effect    = "Allow"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.web3signer_signing_key.arn]
  }
}

resource "aws_iam_role_policy" "external_secrets_signing_reader" {
  name   = "read-validator-keystore"
  role   = aws_iam_role.external_secrets_signing_reader.id
  policy = data.aws_iam_policy_document.external_secrets_signing_reader.json
}
