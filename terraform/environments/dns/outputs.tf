output "portal_hostname" {
  description = "Exact custom hostname requested from the Sites hosting control plane."
  value       = local.portal_hostname
}

output "portal_dns_records" {
  description = "Route 53 names and types managed for the portal custom domain."
  value = {
    for key, record in aws_route53_record.portal : key => {
      fqdn = record.fqdn
      type = record.type
    }
  }
}

output "operations_hostname" {
  description = "Exact hostname reserved for the HTTPS status API and Grafana ingress."
  value       = local.operations_hostname
}

output "operations_acm_certificate_arn" {
  description = "Non-secret ACM certificate ARN supplied to the ingress controller through Flux post-build substitution."
  value       = aws_acm_certificate_validation.operations.certificate_arn
}

output "operations_dns_record" {
  description = "Terraform-owned operations CNAME after an observed NLB hostname is supplied."
  value = try({
    fqdn = aws_route53_record.operations[0].fqdn
    type = aws_route53_record.operations[0].type
  }, null)
}
