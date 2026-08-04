variable "aws_region" {
  description = "AWS region used for provider authentication. Route 53 itself is global."
  type        = string
  default     = "us-west-2"
}

variable "operations_load_balancer_hostname" {
  description = "Observed AWS NLB hostname for the HTTPS operations ingress. Null creates the certificate without publishing the application record."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.operations_load_balancer_hostname == null ||
      can(regex("^[a-z0-9-]+\\.elb\\.[a-z0-9-]+\\.amazonaws\\.com$", var.operations_load_balancer_hostname))
    )
    error_message = "operations_load_balancer_hostname must be null or an AWS ELB hostname without a scheme or path."
  }
}
