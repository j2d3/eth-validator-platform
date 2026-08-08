variable "aws_region" {
  description = "AWS region containing the durable secret containers."
  type        = string
  default     = "us-west-2"
}

variable "project" {
  description = "Resource-name prefix shared with the ephemeral environment."
  type        = string
  default     = "eth-validator-platform"
}

variable "environment" {
  description = "Environment name shared with the ephemeral environment."
  type        = string
  default     = "dev"
}

variable "validator_signing_secret_names" {
  description = "Identity-addressed Secrets Manager suffixes retained across an EKS rebuild."
  type        = map(string)
  default = {
    validator-ephemery-162-01 = "validator-keystore"
    validator-ephemery-162-02 = "validator-keystore-02"
    validator-ephemery-162-03 = "validator-keystore-03"
    validator-ephemery-162-04 = "validator-keystore-04"
    validator-ephemery-162-05 = "validator-keystore-05"
  }
}
