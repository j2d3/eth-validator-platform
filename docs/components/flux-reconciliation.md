# Flux reconciliation

**Owner**: clusters/dev/*.yaml, clusters/local/*.yaml

## Scope

Dependency chain across all Kustomizations, wait/timeout policy, and the fail-closed layer suspend contract.

## Contract

See the code and the referenced runbook below for the current
implementation contract. This page will grow as the platform matures;
for now the authoritative surface is the code plus the runbook.

## Related runbook

- [`eks-flux-bootstrap`](../runbooks/eks-flux-bootstrap.md)

## References

- PRD: [`001-dynamic-validator-platform.md`](../prd/001-dynamic-validator-platform.md)
- Architecture: [`system-overview.md`](../architecture/system-overview.md)
