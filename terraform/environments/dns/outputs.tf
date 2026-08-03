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
