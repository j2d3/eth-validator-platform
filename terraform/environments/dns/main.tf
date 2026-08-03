locals {
  hosted_zone_name = "j2d3.com"
  portal_hostname  = "g.j2d3.com"

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
      records = ["\"openai-site-verification=IqdULfiTB3ESC64Zc45864orM6PGtI2rAaYFdK0SPBQ\""]
    }
    certificate_validation = {
      name    = "_cf-custom-hostname.${local.portal_hostname}"
      type    = "TXT"
      ttl     = 300
      records = ["\"9bbfa39a-d1b6-476a-abcf-a41ef74b312f\""]
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
