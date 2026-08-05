# Networking and ingress

**Owner**: platform/infrastructure/overlays/dev/controllers/, terraform/environments/dns/

## Scope

Ingress-nginx on one internet-facing NLB; exact-host ACM certificates; per-namespace NetworkPolicies; signer DNS+RDS-only egress boundary.

## Contract

See the code and the referenced runbook below for the current
implementation contract. This page will grow as the platform matures;
for now the authoritative surface is the code plus the runbook.

## Related runbook

- [`operations-ingress`](../runbooks/operations-ingress.md)

## References

- PRD: [`001-dynamic-validator-platform.md`](../prd/001-dynamic-validator-platform.md)
- Architecture: [`system-overview.md`](../architecture/system-overview.md)
