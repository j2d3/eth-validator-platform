# Observability and portal

**Owner**: platform/infrastructure/overlays/dev/controllers/monitoring-patch.yaml, platform/apps/portal/dev/, control-plane/portal/

## Scope

kube-prometheus-stack, Grafana provisioning, public status API, portal frontend on Cloudflare Sites, aggregate-only exposure contract.

## Contract

The shared Prometheus release owns cluster-level recording and alert rules;
the per-pair chart owns client metric normalization. For persistent storage,
the shared rules join kubelet filesystem statistics to the PVC's allowlisted
catalog labels on `(namespace, persistentvolumeclaim)`. The normalized series
cover mounted execution, consensus, and validator claims across every client
pair without relying on generated name substrings.

The storage contract records capacity, used bytes, utilization, positive
six-hour growth, and projected seconds to full. Forecasts remain absent until
at least five hours of recording-rule history exists, suppress non-positive or
negligible growth, and alert only when a seven-day projection or 85%
utilization persists for 30 minutes. The validator-detail dashboard shows the
same normalized inputs and makes an absent projection explicit rather than
displaying it as healthy zero.

Only bounded identity labels are exposed through kube-state-metrics. Full BLS
public keys and other sensitive or high-cardinality values are excluded from
the metric-label allowlist.

## Related runbook

- [`portal-telemetry`](../runbooks/portal-telemetry.md)
- [`ethereum-alerts`](../runbooks/ethereum-alerts.md#persistent-volume-capacity)

## References

- PRD: [`001-dynamic-validator-platform.md`](../prd/001-dynamic-validator-platform.md)
- Architecture: [`system-overview.md`](../architecture/system-overview.md)
