# Portal DNS environment

This Terraform root owns public Route 53 and exact-certificate state for the
portal and its operations endpoint. It discovers the existing public
`j2d3.com` hosted zone by name.

| Name | Type | Purpose |
|---|---|---|
| `g.j2d3.com` | `CNAME` | Routes the exact portal hostname to the Sites custom-domain endpoint |
| `_openai-site-verification.g.j2d3.com` | `TXT` | Proves control of the hostname to Sites |
| `_cf-custom-hostname.g.j2d3.com` | `TXT` | Authorizes provider-managed certificate issuance for the hostname |
| ACM validation name | `CNAME` | Validates the exact `ops.g.j2d3.com` ACM certificate |
| `ops.g.j2d3.com` | `CNAME` | Routes to the Kubernetes-created NLB after its observed hostname is supplied |

The verification strings are public DNS values, not credentials. They are
checked in deliberately so the entire DNS contract is recoverable from Git.
Their Terraform values are the unquoted TXT payloads; the AWS provider emits
the Route 53 character-string quoting. Embedding literal quote characters here
would double-quote the request and Route 53 would reject it. There is no
wildcard record, certificate, or custom-domain request.

The operations record is two-phase. When the optional
`operations_load_balancer_hostname` input is omitted, its default is null;
Terraform creates and validates the ACM certificate but does not publish
`ops.g.j2d3.com`. After the ingress Service reports an AWS NLB hostname, a
second reviewed plan supplies that hostname and creates the application CNAME.
Terraform never guesses an NLB name and Kubernetes never writes Route 53.

## Why this is a separate state

DNS has a longer and broader lifecycle than the lab EKS cluster. A portal DNS
edit must not produce an EKS, RDS, or node-group plan, and pausing or replacing
the lab must not disturb the project home. This root therefore uses the same
encrypted state bucket and lock mechanism as the other roots but a distinct
state key: `environments/dns/terraform.tfstate`.

Terraform owns Route 53 and the exact `ops.g.j2d3.com` ACM certificate. Sites
owns the hosted artifact, custom-domain registration, and provider-managed TLS
certificate for `g.j2d3.com`. Kubernetes owns the operations NLB Service.

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
read-only Route 53 query showed that none of the Sites records existed. If a
record appears outside Terraform before the first apply, import it rather than
overwriting or deleting it. Continue with
`docs/runbooks/operations-ingress.md` before supplying an NLB hostname.

## HTTPS qualification

DNS propagation is not certificate evidence. After apply, wait for the Sites
custom-domain status and certificate status to become active, then verify all
of the following:

1. `https://g.j2d3.com` and `https://ops.g.j2d3.com` each present a valid
   certificate whose SAN covers that exact hostname;
2. an HTTP request is redirected to HTTPS or refused, never served as a
   plaintext application response;
3. the portal remains access-controlled until its public-auth boundary is
   deliberately enabled; and
4. the CNAME and both verification TXT records match this root's state.

Do not describe a successful Terraform apply as HTTPS qualification. The
certificate and edge behavior are runtime evidence owned by the hosting
provider.
