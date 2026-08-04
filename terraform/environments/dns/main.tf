locals {
  hosted_zone_name    = "j2d3.com"
  portal_hostname     = "g.j2d3.com"
  operations_hostname = "ops.g.j2d3.com"

  # These public values are issued by the Sites custom-domain control plane.
  # Keeping them here makes the complete DNS contract reviewable and recoverable.
  portal_records = {
    portal = {
      name    = local.portal_hostname
      type    = "CNAME"
      ttl     = 300
      records = ["custom-domains.chatgpt.site."]
    }
    openai_site_verification = {
      name    = "_openai-site-verification.${local.portal_hostname}"
      type    = "TXT"
      ttl     = 300
      records = ["openai-site-verification=IqdULfiTB3ESC64Zc45864orM6PGtI2rAaYFdK0SPBQ"]
    }
    certificate_validation = {
      name    = "_cf-custom-hostname.${local.portal_hostname}"
      type    = "TXT"
      ttl     = 300
      records = ["9bbfa39a-d1b6-476a-abcf-a41ef74b312f"]
    }
  }
}

data "aws_route53_zone" "public" {
  name         = local.hosted_zone_name
  private_zone = false
}

resource "aws_route53_record" "portal" {
  for_each = local.portal_records

  zone_id         = data.aws_route53_zone.public.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = each.value.ttl
  records         = each.value.records
  allow_overwrite = false
}

resource "aws_acm_certificate" "operations" {
  domain_name       = local.operations_hostname
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "operations_certificate_validation" {
  for_each = {
    for option in aws_acm_certificate.operations.domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      type   = option.resource_record_type
      record = option.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.public.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
  allow_overwrite = false
}

resource "aws_acm_certificate_validation" "operations" {
  certificate_arn = aws_acm_certificate.operations.arn
  validation_record_fqdns = [
    for record in aws_route53_record.operations_certificate_validation : record.fqdn
  ]
}

# The ingress controller creates the NLB through Kubernetes. The reviewed
# operator supplies its observed hostname only after the Service reports one;
# Terraform remains the sole writer of the public DNS record.
resource "aws_route53_record" "operations" {
  count = var.operations_load_balancer_hostname == null ? 0 : 1

  zone_id         = data.aws_route53_zone.public.zone_id
  name            = local.operations_hostname
  type            = "CNAME"
  ttl             = 60
  records         = [var.operations_load_balancer_hostname]
  allow_overwrite = false

  depends_on = [aws_acm_certificate_validation.operations]
}
