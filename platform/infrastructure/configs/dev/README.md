# EKS development-environment adapters

Environment-specific Kubernetes adapters for `dev`, the single cost-aware
testnet EKS environment declared in
[`terraform/environments/dev`](../../../../terraform/environments/dev/README.md).
It is the AWS sibling of [`../local`](../local), which holds the same class of
adapter for the `kind` cluster. It declares the application namespaces, the
EKS Pod Identity-backed AWS Secrets Manager interfaces, and the application
chain-data StorageClass. It also declares exact-selector Pod security-group
adapters for Web3Signer and its one-shot migration Job. Secret values and RDS
resources remain outside this directory.

The root Kustomization is the common node substrate: namespaces, default-deny
policies, encrypted gp3, and the Engine-JWT store. `signer/` is a separate,
committed-suspended Flux layer containing the database/signing stores and the
two signer Pod security-group policies. Missing signer/RDS outputs therefore
cannot block common infrastructure or a node-only sync.

## Reconciliation status

**Flux-managed and observed on EKS.** The
[`clusters/dev`](../../../../clusters/dev/README.md) entrypoint reconciles this
directory through the fail-closed dependency chain. The common and signer
infrastructure layers are active; their former committed-suspended bootstrap
state is historical rather than current runtime posture.

`make check` renders the entrypoint and every EKS layer. Focused contract tests
assert that the existing StorageClass is registered rather than duplicated,
the SecretStores use separate engine/database/signing reader roles through
ambient EKS Pod Identity rather than static credentials, the runtime and migration
`SecurityGroupPolicy` selectors stay disjoint, the dependency graph is ordered,
all launch/signing defaults remain off, and common configuration contains none
of the signer-only substitution variables.

`aws-database-secrets` is namespace-visible to both the migration Job in
`database` and Web3Signer in `signing`, but it assumes only the database-reader
role. `aws-signing-secrets` is visible only in `signing` and assumes only the
signing-key reader role. The Web3Signer ExternalSecret consumes one encrypted
validator keystore from that store; node and validator Pods cannot read it.

## What is and is not evidence

The trusted-local AWS apply created the EKS foundation and EBS CSI add-on; Flux
then reconciled this StorageClass. A read-only audit on 2026-08-05 observed:

- one live `ebs-gp3-encrypted` class with the fields documented below;
- 22 bound Ethereum claims using only that class;
- nine 50-GiB execution claims, nine 20-GiB consensus claims, and four 5-GiB
  validator-data claims; and
- retained-volume reattachment during observed Spot replacements.

That evidence proves provisioning, binding, attachment, and replacement-node
reattachment for the current Ephemery workload. It does not prove online
expansion, snapshot restore, AZ-loss recovery, or a funded-mainnet recovery
objective. Those remain separate exercises.

## `ebs-gp3-encrypted`

EKS installs the `ebs.csi.aws.com` driver as a managed add-on but creates no
application StorageClass. A fresh cluster's only class is the EKS-created legacy
`gp2` one. This class is the explicitly-referenced alternative to it.

| Field | Value | Why |
|---|---|---|
| `provisioner` | `ebs.csi.aws.com` | The add-on installs the driver; the class is ours to declare. The in-tree `kubernetes.io/aws-ebs` provisioner is removed from modern Kubernetes. |
| `parameters.type` | `gp3` | AWS's lowest-cost SSD type. Baseline 3,000 IOPS and 125 MiB/s are included in the per-GiB price, and it is ~20% cheaper per GiB than `gp2`. `st1`/`sc1` are cheaper still but are large-sequential HDD types; an active Geth or Lighthouse database is random-I/O and would be badly served by them. |
| `parameters.encrypted` | `"true"` | Encryption at rest for chain data. No `kmsKeyId`, so this requests the account's default EBS key — the same AWS-managed posture the Terraform node root volumes request. An explicit customer-managed KMS decision is still open work and is tracked in the Terraform environment README. |
| `parameters.csi.storage.k8s.io/fstype` | `ext4` | Stated rather than inherited, so a driver-default change cannot silently reformat the contract. `ext4` grows online, which is what makes expansion work without a restart. |
| `volumeBindingMode` | `WaitForFirstConsumer` | Provision the volume in the zone that actually runs the pair. See below. |
| `allowVolumeExpansion` | `true` | Sizes here are a first guess; being able to grow them without recreating a claim is the point. |
| `reclaimPolicy` | `Delete` | See below. |
| default-class annotation | **absent** | Deliberate. |

### It is not a cluster default

The class carries no `storageclass.kubernetes.io/is-default-class` annotation,
and nothing in this repository sets one. A workload that forgets to name a class
gets an unbound claim and a visible failure, rather than silently landing on
`gp2` — or, once a default existed, silently landing on encrypted `gp3` without
anyone having decided that. Every claim names its class.

The chart enforces the other half of that: `charts/ethereum-node/values.yaml`
keeps the local `standard` default, and the EKS profile
`charts/ethereum-node/values-eks-hoodi-storage.yaml` overrides it explicitly.
The reset-aware Ephemery sync profile instead uses
`charts/ethereum-node/values-eks-ephemery.yaml`, which starts at 50 GiB for
Geth and 20 GiB for Lighthouse so one disposable generation does not inherit
the permanent-network cost hypothesis.
`standard` exists in `kind` and not on EKS; `ebs-gp3-encrypted` exists on
EKS and not in `kind`. Neither environment inherits the other's class, and the
chart's `values.schema.json` rejects an empty `storageClassName` — which
Kubernetes would otherwise read as "ignore every StorageClass".

### Zonal binding is a scheduling constraint, not a detail

`WaitForFirstConsumer` defers provisioning until a Pod is scheduled, so the EBS
volume is created in the zone that runs the pair rather than in a zone the
scheduler may not choose. The consequence is durable: **once a claim binds, that
pair instance is pinned to that Availability Zone** for as long as the volume
lives. Capacity replacement, scale-from-zero, and interruption recovery must all
return capacity *in that same zone*, or the pod stays `Pending` with healthy
nodes sitting in other zones.

That is a capacity-model requirement, not a storage one, and it is owned by the
Spot/suspend-resume work rather than by this adapter. PRD §8.6 states the
accepted position: EKS control-plane availability does not make an EBS-backed
validator workload multi-AZ, and the lab accepts restart time during zone
disruption.

A second consequence is local and immediate: a claim under this class provisions
**nothing** until a Pod consumes it. A pair that has never been activated has
`Pending` claims and zero EBS cost. That is the expected state, not a fault —
the local `kind` cluster already shows the same behavior.

### `stopped` retains the claims; `Delete` releases the volume with them

Two different things are easy to conflate here.

**The chart** renders the PVCs in both `active` and `stopped`, and drops them
only in `archived`. So `stopped` is a warm pause: the claims survive, the bound
EBS volumes survive, the same volumes reattach on resume, and **the storage
keeps billing while the compute does not**. If the pair was never activated, see
above — the claims are still `Pending` and there is nothing to bill.

**The reclaim policy** decides what happens when a claim is finally deleted.
`Delete` releases the underlying EBS volume with it. That is correct for these
three claims specifically, and the reason is the data classification rather than
convenience: execution and consensus databases are reproducible by resync (PRD
§14.1 classifies them as replaceable), and signing keys and slashing-protection
history never live on them — those are in Secrets Manager and PostgreSQL, in
different failure domains, by design.

The operational consequence is a sequencing rule for `archived`: the chart
removes the claims, and `Delete` then removes the volumes, so **any snapshot must
be taken and confirmed complete before the archive transition, not after**. The
alternative — `Retain` — would leave released volumes billing indefinitely with
no controller responsible for them, which is the cost failure mode this class
exists to avoid. Whether a snapshot is worth taking is a measured
cost-versus-recovery decision, not an automatic consequence of archive.

### Retain, snapshot, or resync

This decision applies only to replaceable execution, consensus, and validator
client data. Validator keys remain in Secrets Manager and slashing history
remains in PostgreSQL under every option.

| Option | Ongoing storage | Recovery input | Use when |
|---|---|---|---|
| Keep bound PVCs (`stopped`) | Provisioned gp3 continues billing | Existing volumes reattach in-zone | The pause is short or measured recovery-time value exceeds retained-volume cost |
| Snapshot, then archive | Standard-tier snapshot blocks continue billing; volumes are deleted | Restore new volumes, then verify chain identity and sync distance before signing | A measured restore is materially faster than resync and snapshot retention has an owner |
| Archive without snapshot | No chain-volume or snapshot storage | Recreate empty claims and resync/checkpoint-sync with signing disabled | The network is cheap to resync or the pause is long enough that retained storage is not justified |

AWS bills gp3 by provisioned GB-month. The 2026-08-05 live Ephemery fleet has
650 GiB provisioned, so the observed `us-west-2` list input of $0.08/GB-month
is about **$52/month** while all claims remain. Standard EBS snapshots were
$0.05/GB-month at the same observation. Snapshot billing follows written blocks,
not provisioned volume size; PVC filesystem-used bytes are therefore not a
valid snapshot quote. The first snapshot contains all written blocks and later
snapshots are incremental.

Resync cost is not just an instance-hour multiplication. Record elapsed worker
time, the Spot price actually paid, NAT/data-processing and transfer charges,
and missed-duty exposure while the on-chain validator remains active. Current
Spot prices are variable and must be read again when the decision is made.

The operator records these inputs before archive:

1. provisioned gp3 GiB and monthly retention cost;
2. snapshot `FullSnapshotSizeInBytes` or an explicitly conservative upper
   bound, retention period, and restore test time;
3. measured client resync duration and incremental compute/network cost; and
4. the recovery-time objective and expected duty impact.

Do not use EBS Snapshot Archive for a short-lived lab pause. It converts an
incremental snapshot to a full snapshot and has a 90-day minimum retention
cost. It is a separate long-retention decision.

## Hoodi storage profile

`charts/ethereum-node/values-eks-hoodi-storage.yaml` carries the sizes. It is
storage-only: lifecycle state, identity, and telemetry come from the
catalog-generated HelmRelease. The intended wiring, once an EKS apps overlay
exists, is `spec.chart.spec.valuesFiles` listing `charts/ethereum-node/values.yaml`
and this profile — Flux resolves those paths from the GitRepository artifact
root, not the chart directory, so both entries need the chart prefix.

| Claim | Local `kind` default | Hoodi on EKS | Reasoning |
|---|---:|---:|---|
| Execution (Geth) | 20Gi | **200Gi** | The dominant and fastest-growing claim. Sized to leave headroom above a snap-synced Hoodi state so the first sync does not need an expansion mid-flight. |
| Consensus (Lighthouse) | 10Gi | **50Gi** | Beacon database plus blob retention; materially smaller than execution but not trivial. |
| Validator | 5Gi | **5Gi** | Client bookkeeping only. Slashing-protection history lives in PostgreSQL, not here, so this claim has no reason to grow with duties. |

**These numbers are a hypothesis, not a measurement.** They were chosen before
any Hoodi sync has run on this platform — the local 20/10/5 defaults exist to fit
a laptop, and treating them as AWS sizing would be worse. They are **not
production guidance**: they are a starting point for a testnet lab, and the
first real sync is expected to confirm or revise them. What would revise them:
observed on-disk growth per day for both clients, the actual post-sync size of a
snap-synced Hoodi execution database, and blob-retention behavior on the
consensus side. Growth rate matters more than the absolute number, because
expansion is cheap and running out mid-sync is not.

The sizes are also floors in one direction only. `allowVolumeExpansion` grows a
volume; neither EBS nor ext4 shrinks one, and a smaller size requires replacing
the claim and resyncing. A contract test asserts each EKS size is at least the
local default for that reason.

### What this costs

Storage cost is linear in provisioned size, not in used size — an empty 200Gi
volume bills as 200Gi.

The validator claim renders only when a validator client is enabled. A signing
assignment therefore carries all three claims.

```
Hoodi hypothesis: 200Gi + 50Gi + 5Gi = 255 GiB per signing pair
Live Ephemery: 9 x (50Gi + 20Gi) + 4 x 5Gi = 650 GiB

650 GiB x $0.08 per GB-month = about $52 per month
```

The rate was read from the AWS Price List API for `us-west-2` on 2026-08-05; it
is an observation, not a future quote. gp3's 3,000 IOPS / 125 MiB/s baseline is
included at that rate, so the class asks for neither `iops` nor `throughput`.
Provisioning either is billed on top and belongs behind measured queue depth,
latency, or sync-rate evidence.

The pair-level number is not the environment bill. It excludes the EKS control
plane, EC2, NAT, RDS, node root volumes, snapshots, and logs.

## Not covered here

This directory declares storage and the current EKS run has exercised basic
provisioning, binding, and retained-volume reattachment. The following remain
explicitly out of scope or unqualified:

- **Node root-volume sizing.** Terraform's business; unchanged by this adapter.
- **RDS storage.** The slashing database is a separate failure domain with its
  own sizing, autoscaling ceiling, and backup posture.
- **EBS CloudWatch performance metrics** — PVC capacity, use, growth, and
  projected-full alerts are live; EBS IOPS, throughput, queue length, and
  latency still require an AWS metrics adapter.
- **Runtime qualification**: online expansion without claim recreation,
  snapshot/restore timing, and whether the 200/50/5 Hoodi hypothesis survives a
  real sync.

Those are tracked on the storage issue this adapter partially satisfies.

## Pricing references

- [Amazon EBS pricing](https://aws.amazon.com/ebs/pricing/)
- [How EBS snapshots work](https://docs.aws.amazon.com/ebs/latest/userguide/how_snapshots_work.html)
- [EBS snapshot archive guidelines](https://docs.aws.amazon.com/ebs/latest/userguide/archiving-guidelines.html)
- [EC2 Spot pricing](https://aws.amazon.com/ec2/spot/pricing/)
