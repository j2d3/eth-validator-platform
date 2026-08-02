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
  description = "CIDRs permitted to reach the public EKS API endpoint. GitHub Actions supplies its current /32 during apply."
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

variable "ethereum_instance_types" {
  description = "On-demand, memory-optimized instance types for EL/CL nodes."
  type        = list(string)
  default     = ["r7i.2xlarge", "r6i.2xlarge"]
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
