# Portal DNS environment

This Terraform root owns the public Route 53 records for the operator portal's
single custom hostname: **`g.j2d3.com`**. It discovers the existing public
`j2d3.com` hosted zone by name and manages exactly three records:

| Name | Type | Purpose |
|---|---|---|
| `g.j2d3.com` | `CNAME` | Routes the exact portal hostname to the Sites custom-domain endpoint |
| `_openai-site-verification.g.j2d3.com` | `TXT` | Proves control of the hostname to Sites |
| `_cf-custom-hostname.g.j2d3.com` | `TXT` | Authorizes provider-managed certificate issuance for the hostname |

The verification strings are public DNS values, not credentials. They are
checked in deliberately so the entire DNS contract is recoverable from Git.
There is no wildcard record and no wildcard custom-domain request.

## Why this is a separate state

DNS has a longer and broader lifecycle than the lab EKS cluster. A portal DNS
edit must not produce an EKS, RDS, or node-group plan, and pausing or replacing
the lab must not disturb the project home. This root therefore uses the same
encrypted state bucket and lock mechanism as the other roots but a distinct
state key: `environments/dns/terraform.tfstate`.

Terraform owns Route 53 only. Sites owns the hosted artifact, custom-domain
registration, and provider-managed TLS certificate. No ACM certificate is
declared here because Route 53 is not the TLS endpoint and this repository does
not operate the hosting provider's edge.

## Trusted-local plan and apply

There is intentionally no GitHub Actions apply workflow. From an authenticated
operator workstation:

```bash
cp terraform/environments/dns/backend.hcl.example terraform/environments/dns/backend.hcl
terraform -chdir=terraform/environments/dns init -backend-config=backend.hcl
terraform -chdir=terraform/environments/dns plan -out=portal-dns.tfplan
terraform -chdir=terraform/environments/dns apply portal-dns.tfplan
```

Review the saved plan before applying. At the time this root was authored, a
read-only Route 53 query showed that none of the three records existed. If a
record appears outside Terraform before the first apply, import it rather than
overwriting or deleting it.

## HTTPS qualification

DNS propagation is not certificate evidence. After apply, wait for the Sites
custom-domain status and certificate status to become active, then verify all
of the following:

1. `https://g.j2d3.com` presents a valid certificate whose SAN covers the exact
   hostname;
2. an HTTP request is redirected to HTTPS or refused, never served as a
   plaintext application response;
3. the portal remains access-controlled until its public-auth boundary is
   deliberately enabled; and
4. the CNAME and both verification TXT records match this root's state.

Do not describe a successful Terraform apply as HTTPS qualification. The
certificate and edge behavior are runtime evidence owned by the hosting
provider.
