# Observability and portal

**Owner**: platform/infrastructure/overlays/dev/controllers/monitoring-patch.yaml, platform/apps/portal/dev/, control-plane/portal/

## Scope

kube-prometheus-stack, Grafana provisioning, public status API, portal frontend on Cloudflare Sites, aggregate-only exposure contract.

## Contract

See the code and the referenced runbook below for the current
implementation contract. This page will grow as the platform matures;
for now the authoritative surface is the code plus the runbook.

## Related runbook

- [`portal-telemetry`](../runbooks/portal-telemetry.md)

## References

- PRD: [`001-dynamic-validator-platform.md`](../prd/001-dynamic-validator-platform.md)
- Architecture: [`system-overview.md`](../architecture/system-overview.md)
