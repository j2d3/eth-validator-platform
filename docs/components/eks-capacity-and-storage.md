# EKS capacity and storage

**Owner**: Terraform node-group definitions in
`terraform/environments/dev/main.tf` + `variables.tf`, cluster StorageClasses
in `platform/infrastructure/configs/dev/`, and per-pair capacity overlays under
`platform/apps/nodes/dev/` and `charts/ethereum-node/values-eks-*.yaml`.

## The node-group topology

Four managed node groups on one EKS 1.35 cluster in `us-west-2`:

| Group | Purpose | Capacity type | AZs | Instance-type list | Sizing |
|---|---|---|---|---|---|
| `system` | Flux, ESO, cert-manager, kube-prometheus-stack, portal | `ON_DEMAND` | All 3 AZs | `m7i.large`, `m6i.large` | min 2 / max 4 / desired 2 |
| `ethereum-<az>` (one per AZ) | EL + CL client pairs | `var.ethereum_capacity_type` — the module variable defaults to `ON_DEMAND`, while the applied `dev` environment uses `SPOT` | Single-AZ per group | `r8i.2xlarge`, `r8a.2xlarge`, `r7i.2xlarge`, `r7a.2xlarge`, `r6i.2xlarge`, `r6a.2xlarge` (six x86 memory-optimized pools, three-family minimum enforced by variable validation) | min 0 / max `var.ethereum_max_size_per_az` (bounded 1–3 after #142) / desired 1 in `var.ethereum_initial_active_az_index` at creation, 0 in the other two |

Two structural choices worth calling out:

- **System vs Ethereum tiers are separate node groups.** System workloads
  need multi-AZ scheduling; Ethereum pairs are individually stateful and
  each own AZ-local EBS. Separating the tiers lets each get its own
  capacity/instance-family policy without compromising the other.
- **One node group per AZ for the Ethereum tier.** EBS volumes are
  AZ-local; a Pod can't move across AZs without abandoning its PV.
  Pinning each group to one AZ makes the Pod-to-volume binding stable.
  The pinned EKS module deliberately ignores post-creation `desired_size`
  changes so the operator or a future autoscaler can grow one group in-AZ
  without Terraform fighting it.

## Capacity type: module default is ON_DEMAND; dev is currently SPOT

The Ethereum tier's capacity type is controlled by
`var.ethereum_capacity_type`. State both facts directly:

- The reusable module variable **defaults to `ON_DEMAND`** — the
  variable's description says "ON_DEMAND remains the default until the
  explicit Spot interruption exercise is qualified."
- The **applied `dev` environment currently sets it to `SPOT`**. Multiple
  observed replacements reattached retained EBS claims and returned pairs to
  Ready; one signing validator remained disabled until doppelganger detection
  cleared. This is useful evidence, but not an FIS drill, an AZ-capacity-drought
  test, or proof of zero missed duties.

- **Observed price input.** On 2026-08-05, the diversified allowed pools in
  `us-west-2` ranged from about $0.157 to $0.247 per worker-hour. The live tier
  had nine Spot workers; multiply the price of the instance type actually
  allocated rather than treating the range as a bill. Spot prices and capacity
  change, so this is an observation rather than a forecast.
- **Remaining pitfalls.** Interruption timing relative to client flush;
  single-AZ groups being
  uncoverable during AZ-level Spot capacity droughts (partially
  mitigated by the six-pool instance diversification enforced above,
  which already validates at ≥3 distinct pools); and duty impact during
  replacement gaps.
- **What a production Spot rollout would still need.**
  A `terminationGracePeriodSeconds` uplift on the client
  StatefulSets — the chart does not currently set one. Consensus-side
  `preStop` hooks — none exist. Capacity-rebalance events routed to
  drain hooks — not wired.

The lab has demonstrated replacement and reattachment, not a production
availability objective. A production design still needs an explicit mixed
capacity model, interruption handling, and duty-level SLO evidence.

## Storage: EBS gp3 with a single StorageClass

- Single StorageClass `ebs-gp3-encrypted` (ext4,
  `WaitForFirstConsumer`, `reclaimPolicy: Delete`, `encrypted: "true"`)
  is the only StorageClass Ethereum pairs use.
- Encryption uses the AWS-managed EBS default key at present; a
  customer-managed KMS key is not declared. See
  [`terraform-aws-foundation`](terraform-aws-foundation.md) for the
  actual encryption inventory vs future work.
- PVC naming embeds the Ephemery **generation** (currently
  `generation-162`) into the chart-generated names via the
  `identityFingerprint` truncation. A genesis-reset cycle therefore
  creates new PVCs rather than silently reusing stale genesis chain
  state.
- A Pod eviction does not delete its PVC, so the new Pod reattaches the same
  volume. `reclaimPolicy: Delete` applies only when an archive deletes the PVC;
  this releases replaceable chain data instead of leaving orphaned EBS cost.
- Prometheus records used bytes, capacity, utilization, six-hour positive
  growth, and projected time to full for every mounted platform PVC. The
  assignment is joined through the PVC's catalog labels rather than inferred
  from its generated name. See the
  [capacity alert procedure](../runbooks/ethereum-alerts.md#persistent-volume-capacity).
- The current fleet has 22 bound claims totaling 650 GiB: nine 50-GiB
  execution, nine 20-GiB consensus, and four 5-GiB validator-data claims. At
  the observed 2026-08-05 `us-west-2` gp3 list input of $0.08/GB-month, retaining
  all claims is about $52/month before snapshots or other AWS services.
- Archive policy is measured rather than automatic: retain volumes for a short
  pause, snapshot/delete only when measured restore time justifies snapshot
  retention, or delete/resync when regenerating the network state is cheaper.
  Snapshot cost follows written blocks rather than provisioned GiB, so PVC-used
  bytes are not presented as a snapshot quote. The detailed operator inputs are
  in the [development adapter README](../../platform/infrastructure/configs/dev/README.md#retain-snapshot-or-resync).

## What EKS-specific values live where

- Node-group definitions, IAM, and subnet placement: Terraform under
  `terraform/environments/dev/`.
- StorageClass, cluster-wide policies, and namespace/label
  scaffolding: Flux-managed under
  `platform/infrastructure/configs/dev/`.
- Per-pair capacity overlays (resource requests, PVC size, telemetry,
  Engine-JWT wiring): under `platform/apps/nodes/dev/` HelmRelease
  `values` and `charts/ethereum-node/values-eks-*.yaml`.

## References

- Runbook: [`eks-capacity`](../runbooks/eks-capacity.md)
- Terraform: [`terraform-aws-foundation`](terraform-aws-foundation.md)
- Chart: [`ethereum-node-chart`](ethereum-node-chart.md)
- Architecture: [`system-overview`](../architecture/system-overview.md)
