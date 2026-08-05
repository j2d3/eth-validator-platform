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
| `ethereum-<az>` (one per AZ) | EL + CL client pairs | `var.ethereum_capacity_type` — the module variable defaults to `ON_DEMAND`, but the applied `dev` environment currently sets it to `SPOT` (Spot's interruption resilience is not yet qualified against a real drill) | Single-AZ per group | `r8i.2xlarge`, `r8a.2xlarge`, `r7i.2xlarge`, `r7a.2xlarge`, `r6i.2xlarge`, `r6a.2xlarge` (six x86 memory-optimized pools, three-family minimum enforced by variable validation) | min 0 / max `var.ethereum_max_size_per_az` (bounded 1–3 after #142) / desired 1 in `var.ethereum_initial_active_az_index` at creation, 0 in the other two |

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

## Capacity type: module default is ON_DEMAND; dev is currently SPOT (unqualified)

The Ethereum tier's capacity type is controlled by
`var.ethereum_capacity_type`. State both facts directly:

- The reusable module variable **defaults to `ON_DEMAND`** — the
  variable's description says "ON_DEMAND remains the default until the
  explicit Spot interruption exercise is qualified."
- The **applied `dev` environment currently sets it to `SPOT`**.
  Interruption resilience has not been drilled against a doppelganger
  event, a mid-flush eviction, or an AZ-level Spot capacity drought. The
  Spot economics discussion below is the *cost/tradeoff* record for that
  setting; the pitfalls listed are live concerns for the dev
  environment, not hypothetical future concerns.

- **Approximate savings.** Spot for `r8i.2xlarge` in `us-west-2` at
  recent snapshots runs roughly 60–75% under on-demand. At current
  Ethereum-tier size (one active node in one AZ), that is on the order
  of $200/month savings vs on-demand — a lab number, not a fleet
  number.
- **Live pitfalls (not yet drilled in dev).** Interruption at
  ~30 seconds from AWS's termination signal; single-AZ groups being
  uncoverable during AZ-level Spot capacity droughts (partially
  mitigated by the six-pool instance diversification enforced above,
  which already validates at ≥3 distinct pools); doppelganger risk on
  signing pairs that Web3Signer + RDS is expected to catch but has
  never been drilled against.
- **What we do not have today that a prod Spot rollout would need.**
  A `terminationGracePeriodSeconds` uplift on the client
  StatefulSets — the chart does not currently set one. Consensus-side
  `preStop` hooks — none exist. Capacity-rebalance events routed to
  drain hooks — not wired.

So the honest current answer to "does Spot have a place in a production
validator stack" is: probably yes, in a mixed on-demand-primary + Spot-
redundant shape with capacity-rebalance and PDBs, but this repo has
demonstrated none of those pieces. It is future work.

## Storage: EBS gp3 with a single StorageClass

- Single StorageClass `ephemery-gp3-encrypted` (xfs,
  `WaitForFirstConsumer`, `reclaimPolicy: Retain`, `encrypted: "true"`)
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
- `reclaimPolicy: Retain` is deliberate: a Pod eviction must not
  cascade into PV deletion. The new Pod reattaches by PVC name.
- Prometheus records used bytes, capacity, utilization, six-hour positive
  growth, and projected time to full for every mounted platform PVC. The
  assignment is joined through the PVC's catalog labels rather than inferred
  from its generated name. See the
  [capacity alert procedure](../runbooks/ethereum-alerts.md#persistent-volume-capacity).

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
