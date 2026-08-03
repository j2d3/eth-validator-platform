data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name = "${var.project}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)

  tags = merge(
    {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "Terraform"
      Repository  = "j2d3/eth-validator-platform"
    },
    var.tags
  )
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.6.1"

  name = local.name
  cidr = var.vpc_cidr

  azs             = local.azs
  private_subnets = [for index, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, index)]
  public_subnets  = [for index, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, index + 128)]
  intra_subnets   = [for index, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, index + 192)]

  enable_nat_gateway     = true
  single_nat_gateway     = var.single_nat_gateway
  one_nat_gateway_per_az = !var.single_nat_gateway
  enable_dns_hostnames   = true
  enable_dns_support     = true

  enable_flow_log                      = true
  create_flow_log_cloudwatch_iam_role  = true
  create_flow_log_cloudwatch_log_group = true
  flow_log_max_aggregation_interval    = 60

  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
  }

  tags = local.tags
}

data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${local.name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.24.1"

  name               = local.name
  kubernetes_version = var.kubernetes_version

  endpoint_private_access      = true
  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.cluster_public_access_cidrs

  authentication_mode                      = "API"
  enable_cluster_creator_admin_permissions = true
  access_entries                           = var.access_entries

  enabled_log_types                      = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  cloudwatch_log_group_retention_in_days = 30

  addons = {
    coredns = {}
    eks-pod-identity-agent = {
      before_compute = true
    }
    kube-proxy = {}
    vpc-cni = {
      before_compute = true
    }
    aws-ebs-csi-driver = {
      pod_identity_association = [{
        role_arn        = aws_iam_role.ebs_csi.arn
        service_account = "ebs-csi-controller-sa"
      }]
    }
  }

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.intra_subnets

  eks_managed_node_groups = merge(
    {
      system = {
        ami_type       = "AL2023_x86_64_STANDARD"
        capacity_type  = "ON_DEMAND"
        instance_types = var.system_instance_types

        min_size     = 2
        max_size     = 4
        desired_size = 2

        labels = {
          workload = "system"
        }

        update_config = {
          max_unavailable_percentage = 25
        }

        block_device_mappings = {
          xvda = {
            device_name = "/dev/xvda"
            ebs = {
              encrypted             = true
              delete_on_termination = true
              volume_size           = var.system_root_volume_size_gib
              volume_type           = "gp3"
            }
          }
        }
      }
    },
    {
      # EBS volumes are zonal. One zero-minimum group per AZ ensures a later
      # autoscaler can launch capacity where an existing PVC is bound. Until
      # that controller is qualified, exactly one operator-selected AZ receives
      # the lab's bounded desired capacity.
      for index, az in local.azs : "ethereum-${az}" => {
        ami_type       = "AL2023_x86_64_STANDARD"
        capacity_type  = var.ethereum_capacity_type
        instance_types = var.ethereum_instance_types
        subnet_ids     = [module.vpc.private_subnets[index]]

        min_size     = 0
        max_size     = var.ethereum_max_size_per_az
        desired_size = index == var.ethereum_initial_active_az_index ? var.ethereum_initial_desired_size : 0

        labels = {
          workload                                = "ethereum"
          "platform.galaxy-lab/availability-zone" = az
        }

        taints = {
          dedicated = {
            key    = "dedicated"
            value  = "ethereum"
            effect = "NO_SCHEDULE"
          }
        }

        update_config = {
          max_unavailable = 1
        }

        block_device_mappings = {
          xvda = {
            device_name = "/dev/xvda"
            ebs = {
              encrypted             = true
              delete_on_termination = true
              volume_size           = var.ethereum_root_volume_size_gib
              volume_type           = "gp3"
            }
          }
        }
      }
    }
  )

  tags = local.tags
}

resource "aws_secretsmanager_secret" "engine_jwt" {
  name                    = "${local.name}/ethereum/engine-jwt"
  description             = "EL/CL Engine API JWT; value is injected after Terraform apply and is not stored in state."
  recovery_window_in_days = 7
}

resource "aws_iam_role" "external_secrets" {
  name               = "${local.name}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

data "aws_iam_policy_document" "external_secrets" {
  statement {
    sid       = "ReadEngineJwt"
    effect    = "Allow"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.engine_jwt.arn]
  }
}

resource "aws_iam_role_policy" "external_secrets" {
  name   = "read-engine-jwt"
  role   = aws_iam_role.external_secrets.id
  policy = data.aws_iam_policy_document.external_secrets.json
}

resource "aws_eks_pod_identity_association" "external_secrets" {
  cluster_name    = module.eks.cluster_name
  namespace       = "external-secrets"
  service_account = "external-secrets"
  role_arn        = aws_iam_role.external_secrets.arn
}
