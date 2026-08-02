variable "aws_region" {
  description = "AWS region in which to deploy the lab."
  type        = string
  default     = "us-west-2"
}

variable "project" {
  description = "Resource-name prefix."
  type        = string
  default     = "eth-validator-platform"
}

variable "environment" {
  description = "Environment name."
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  description = "RFC1918 CIDR for the VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "kubernetes_version" {
  description = "EKS version. Pin to a version supported by the selected Flux release."
  type        = string
  default     = "1.35"
}

variable "cluster_public_access_cidrs" {
  description = "CIDRs permitted to reach the public EKS API endpoint. Supply a trusted operator /32 during the local plan/apply, or use private connectivity."
  type        = list(string)
  default     = ["127.0.0.1/32"]

  validation {
    condition     = alltrue([for cidr in var.cluster_public_access_cidrs : cidr != "0.0.0.0/0"])
    error_message = "Do not expose the Kubernetes API to 0.0.0.0/0; supply trusted /32 or private runner CIDRs."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway to control lab cost. Set false for one per AZ in a production-shaped environment."
  type        = bool
  default     = true
}

variable "system_instance_types" {
  description = "Instance types for platform controllers."
  type        = list(string)
  default     = ["m7i.large", "m6i.large"]
}

variable "system_root_volume_size_gib" {
  description = "Disposable encrypted gp3 root volume size for each system node. Platform state belongs in managed services or PVCs, not the node root."
  type        = number
  default     = 40

  validation {
    condition     = var.system_root_volume_size_gib >= 20
    error_message = "System node roots must be at least 20 GiB."
  }
}

variable "ethereum_instance_types" {
  description = "Diversified x86_64 memory-optimized instance types with equivalent 8-vCPU/64-GiB scheduling capacity for EL/CL nodes."
  type        = list(string)
  default = [
    "r8i.2xlarge",
    "r8a.2xlarge",
    "r7i.2xlarge",
    "r7a.2xlarge",
    "r6i.2xlarge",
    "r6a.2xlarge",
  ]

  validation {
    condition     = length(var.ethereum_instance_types) >= 3 && length(distinct(var.ethereum_instance_types)) == length(var.ethereum_instance_types)
    error_message = "Supply at least three distinct, scheduling-equivalent Ethereum instance types so Spot is not tied to one capacity pool."
  }
}

variable "ethereum_capacity_type" {
  description = "EKS capacity type for zonal Ethereum groups. ON_DEMAND remains the default until the explicit Spot interruption exercise is qualified."
  type        = string
  default     = "ON_DEMAND"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.ethereum_capacity_type)
    error_message = "ethereum_capacity_type must be ON_DEMAND or SPOT."
  }
}

variable "ethereum_initial_active_az_index" {
  description = "Index into the three configured AZs whose Ethereum group receives capacity when it is first created. Existing managed-node-group desired size is operated through the EKS API, not Terraform."
  type        = number
  default     = 0

  validation {
    condition     = var.ethereum_initial_active_az_index >= 0 && var.ethereum_initial_active_az_index < 3 && floor(var.ethereum_initial_active_az_index) == var.ethereum_initial_active_az_index
    error_message = "ethereum_initial_active_az_index must be one of 0, 1, or 2."
  }
}

variable "ethereum_initial_desired_size" {
  description = "Desired nodes in the selected Ethereum AZ at managed-node-group creation only. The pinned EKS module intentionally ignores later desired-size changes."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1], var.ethereum_initial_desired_size)
    error_message = "The lab permits zero or one initial Ethereum node; broaden this bound only with a reviewed cost/capacity change."
  }
}

variable "ethereum_max_size_per_az" {
  description = "Hard cost bound for each zonal Ethereum group. Zero-minimum groups in non-selected AZs remain available for a later autoscaler qualification."
  type        = number
  default     = 1

  validation {
    condition     = var.ethereum_max_size_per_az >= 1 && var.ethereum_max_size_per_az <= 2 && floor(var.ethereum_max_size_per_az) == var.ethereum_max_size_per_az
    error_message = "ethereum_max_size_per_az must be one or two."
  }
}

variable "ethereum_root_volume_size_gib" {
  description = "Disposable encrypted gp3 root volume size for each Ethereum node. Chain databases use separate EBS CSI PVCs."
  type        = number
  default     = 30

  validation {
    condition     = var.ethereum_root_volume_size_gib >= 20
    error_message = "Ethereum node roots must be at least 20 GiB."
  }
}

variable "access_entries" {
  description = "EKS access entries. Prefer mapped IAM roles over permanent IAM users."
  type = map(object({
    principal_arn     = string
    kubernetes_groups = optional(list(string))
    policy_associations = optional(map(object({
      policy_arn = string
      access_scope = object({
        type       = string
        namespaces = optional(list(string))
      })
    })), {})
  }))
  default = {}
}

variable "tags" {
  description = "Additional tags."
  type        = map(string)
  default     = {}
}
