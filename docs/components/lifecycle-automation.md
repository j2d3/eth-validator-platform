# Lifecycle automation

**Owner**: .github/workflows/*, hack/merge-pr.sh

## Scope

GitHub Actions non-signing lifecycle form; stopped ↔ active transitions with PVC preservation; merge-wrapper safety.

## Contract

See the code and the referenced runbook below for the current
implementation contract. This page will grow as the platform matures;
for now the authoritative surface is the code plus the runbook.

## References

- PRD: [`001-dynamic-validator-platform.md`](../prd/001-dynamic-validator-platform.md)
- Architecture: [`system-overview.md`](../architecture/system-overview.md)
