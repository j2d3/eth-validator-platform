# Development environment

This root declares the single production-shaped but cost-aware testnet **Amazon
EKS** environment. It contains no Google/GKE adapter. Workers are private; the
API endpoint is private plus public access restricted to CIDRs supplied by a
trusted operator. The system group is separated from tainted, zonal Ethereum
capacity. One zero-minimum managed node group per Availability Zone preserves
the ability to launch a worker where an EBS volume is bound. On first creation,
one selected group receives at most one lab node; subsequent desired capacity
is operated through the EKS API until an autoscaler is separately qualified.

Two trusted-local applies completed on 2026-08-02: the foundation itself, then
the zonal Spot replacement for the single on-demand Ethereum group. That is
runtime evidence for the foundation described below; it is not evidence for the
still-missing RDS, Flux, External Secrets, application-storage, P2P, or
validator paths.

## Operating boundary

Version 1 is planned and applied from a trusted local workstation using the
operator's existing AWS authentication. There is intentionally no GitHub
Actions Terraform apply/destroy workflow and no AWS credential or OIDC trust in
the application workflows. GitHub Actions validates Terraform and creates
reviewed application/catalog changes; after cluster bootstrap, Flux is the
continuous writer for in-cluster applications.

Terraform owns the AWS foundation only. It does not continuously manage Helm
releases, dashboards, validator assignments, or node-pair lifecycle state.

## Declared, observed, and missing

| Area | Declared in this root | Runtime evidence on 2026-08-02 | Still required before Phase 4 exit |
|---|---|---|---|
| Networking | Three-AZ VPC; public, private worker, and intra control-plane subnets; DNS; flow logs; one NAT gateway by lab default | VPC, nine subnets, routes, flow logs, and the single-NAT lab path were created by the reviewed 90-resource plan | Sustained routing/egress evidence, VPC endpoints/cost decision, production NAT/AZ posture |
| EKS | Restricted public plus private API; control-plane logs; access-entry input | Kubernetes 1.35 control plane created; the dedicated kubeconfig reached the restricted endpoint; post-apply Terraform plan reported no drift | Upgrade, API-throttling, access-role, and private-connectivity exercises |
| Capacity | Two-node on-demand system group; three tainted, zonal, zero-minimum Ethereum groups; explicit ON_DEMAND/SPOT selector; at most one initially selected Ethereum node; Terraform hard bounds plus EKS-API desired-size ownership | The first baseline had two Ready `m7i.large` system nodes and one Ready `r7i.2xlarge` on-demand Ethereum node. The zonal/Spot replacement was then applied — `21 added, 2 changed, 7 destroyed` — leaving all four managed groups `ACTIVE` with no health issues: a two-node on-demand system group of Ready `m7i.large` nodes in `us-west-2b`/`us-west-2c`, and one Spot Ethereum group per AZ. The active `us-west-2a` group obtained a Ready `r8i.2xlarge` Spot node — the pool's first-preference type, so fallback is still unexercised. An EKS `UpdateNodegroupConfig` call then paused that group to zero; all three Ethereum groups now report `0/1`. Both the post-apply and post-pause plans reported no changes | Guarded EKS status/pause/resume command; Spot/FIS interruption test; same-AZ resume with a bound chain PVC; later Karpenter decision |
| Add-ons and identity | VPC CNI, CoreDNS, kube-proxy, EKS Pod Identity agent, EBS CSI with a dedicated role | All five managed add-ons and their pods were Running; EBS CSI controller/node pods were fully Ready with zero restarts. The application `gp3` StorageClass was accepted by a server-side dry-run against this cluster, but it was not persisted, and no application PVC or EBS volume exists | AWS External Secrets `SecretStore`, Flux EKS overlay, and registration plus runtime qualification of the application EBS StorageClass — the class manifest itself is declared and unreconciled in [`platform/infrastructure/configs/dev`](../../../platform/infrastructure/configs/dev/README.md) |
| Secrets | Empty Secrets Manager container for the EL/CL Engine JWT; read-only External Secrets role scoped to it | Secret container, role, policy, and Pod Identity association exist; no secret value was created | Restricted operator value bootstrap, signing-key containers/policies, rotation evidence |
| Slashing database | Not declared | None | RDS PostgreSQL, subnet/security groups, credentials adapter, backups/PITR, restore and failover qualification |
| Encryption | AWS-managed encryption is requested for node root volumes and Terraform state | Remote state controls were applied; every observed node root was encrypted gp3 at baseline 3,000 IOPS / 125 MiB/s, and after the replacement they measured 40 GiB per system node and 30 GiB per Ethereum node | Explicit KMS/AWS-managed-key decision for RDS, EBS application data, and Secrets Manager |
| Public Ethereum networking | Not declared | None | P2P Service/NLB design, source ranges, discovery/TCP/UDP qualification |

Local CloudNativePG, local-path volumes, and the Kubernetes External Secrets
provider are contract-compatible development adapters; they are not evidence
for RDS, EBS, IAM, KMS, or EKS behavior.

## Trusted local plan/apply

Set `cluster_public_access_cidrs` to the operator workstation's trusted `/32`
(or use private connectivity). Keep the checked-in loopback default until that
decision is explicit; it makes an accidental public-API apply inaccessible
rather than broadly exposed.

```bash
cp terraform/environments/dev/backend.hcl.example terraform/environments/dev/backend.hcl
cp terraform/environments/dev/terraform.tfvars.example terraform/environments/dev/terraform.tfvars
terraform -chdir=terraform/environments/dev init -backend-config=backend.hcl
terraform -chdir=terraform/environments/dev plan
```

Review and save every plan before a manual apply. Both 2026-08-02 applies used
this path and each was followed by a zero-drift plan; do not add an automatic
apply merely to avoid the operator checkpoint. The root is not yet a Phase
4-complete environment; the table above is the remaining work list.

## Zonal Ethereum capacity and pause semantics

EBS volumes can attach only to nodes in their own Availability Zone. The root
therefore declares an Ethereum managed node group in each of the three private
subnets rather than one group spanning all subnets. Every group has
`min_size = 0`; on creation only, `ethereum_initial_active_az_index` receives
`ethereum_initial_desired_size`, which is bounded to zero or one. Before a
chain PVC binds, choose the initial index deliberately.

The pinned EKS module intentionally places managed-node-group `desired_size` in
Terraform `ignore_changes`. That lets an autoscaler or explicit operational
command own live capacity, but it also means changing either initial variable
after creation produces no scaling action. Terraform owns the three groups,
their minimum/maximum bounds, capacity type, instance pools, and launch
templates. A bounded EKS `UpdateNodegroupConfig` operation owns live desired
size until Karpenter or Cluster Autoscaler is qualified. Live state must be read
from EKS; Terraform outputs deliberately do not publish declared desired size
as though it were observed status.

`ethereum_capacity_type` defaults to `ON_DEMAND`. Set it to `SPOT` in the
reviewed operator inputs for the testnet interruption experiment. The default
does not change until the Spot exercise proves forced termination, EBS
reattachment, sync recovery, and fail-closed signing behavior. The instance list
contains scheduling-equivalent 8-vCPU/64-GiB Intel and AMD families so Spot is
not tied to one capacity pool.

A warm pause is not a Terraform variable edit: first merge a stopped
assignment, wait until client pods are absent, and only then run the reviewed,
bounded EKS scaling operation that sets the active group to zero. The guarded
command is follow-up work; until it exists, use the EKS API only through an
individually reviewed operator action — the route the one observed pause below
took. EKS, NAT, system nodes, EBS, logs, and later RDS continue to cost money
while validator compute is paused. Resume sets
one group to one in the PVC's AZ, waits for node readiness, volume attachment,
and client sync, and does not authorize signing.

The node roots are disposable encrypted gp3 volumes: 40 GiB per system node and
30 GiB per Ethereum node by default. Execution and consensus databases belong
on separate EBS CSI PVCs, so increasing root disks to hold chain data would be a
cost and recovery bug. Changing a root size publishes a new launch-template
version; applying it asks EKS to roll the affected managed node group. It does
not shrink a running instance's EBS volume in place.

### Applied replacement evidence

The reviewed replacement plan against the 90-resource 2026-08-02 state, with Spot
selected, reported **21 additions, 2 in-place changes, and 7 deletions**, and was
applied later the same day from the trusted-local path. It:

- created one Spot Ethereum group in each of `us-west-2a`, `us-west-2b`, and
  `us-west-2c`, with initial desired sizes `1`, `0`, and `0` respectively;
- removed the old single on-demand Ethereum group and its dedicated IAM,
  launch-template, and module-validation resources; and
- published the 40-GiB system launch-template version and updated the system
  group to it, causing an EKS-managed node rollout.

Observed afterwards: all four managed node groups `ACTIVE` with no reported
health issues; the initially active `us-west-2a` group carrying a Ready
`r8i.2xlarge` Spot node; the two replacement system nodes Ready as `m7i.large`
in `us-west-2b` and `us-west-2c`; every EKS system pod Running with zero
restarts; encrypted baseline gp3 roots at the intended 40 GiB and 30 GiB; and a
post-apply plan reporting no changes.

`r8i.2xlarge` is the *first* entry in `ethereum_instance_types`, so this run
proves that the diversified pool is accepted end to end and that Spot fulfilled
the top preference in `us-west-2a` at that moment. It does not prove fallback:
no substitution to a later family was observed, because none was needed. The
Spot/FIS interruption exercise is what would test the remaining five entries.

The old and new Ethereum modules were independent graph branches, so the plan
did not promise create-before-destroy ordering between them. That was acceptable
only because the lab had no application workloads or chain PVCs at the time. A
later migration with live clients must be staged: stop the assignment, verify
client-pod absence, create and qualify replacement capacity in the PVC's AZ, and
only then remove the old group.

On the same date, AWS reported that every declared Ethereum instance type was
offered in all three selected Availability Zones. That proves API availability,
not Spot capacity at the moment of a future apply.

### Observed pause to zero

With that fleet up, the pause path was exercised once by hand on the empty lab.
After confirming that no application pods, StatefulSets, or PersistentVolumeClaims
existed, a bounded EKS `UpdateNodegroupConfig` call set the `us-west-2a` Ethereum
group's desired size to zero. It succeeded; all three Ethereum groups now report
`0/1` and only the two system nodes remain.

A second Terraform plan taken after that pause also reported no changes. That is
the ownership boundary proving itself rather than being asserted: Terraform still
declares an initial desired size of one for the selected group, EKS reports zero,
and Terraform does not try to reconcile the difference. An operational pause
survives a subsequent plan.

What this does not evidence: resume has not been exercised, and neither has a
pause with a bound chain PVC — the volume whose Availability Zone the whole
design exists to protect did not exist during this run. Both belong to the
guarded command that is still follow-up work.

### Cost snapshot, not a quote

The 2026-08-02 `us-west-2` observation put the original fixed baseline at about
$0.876/hour before application PVCs, RDS, log ingestion, NAT data processing,
or transfer: two `m7i.large` nodes, one `r7i.2xlarge`, the EKS control plane,
and one NAT gateway. Compatible 8-vCPU/64-GiB Spot pools were approximately
$0.19–$0.23/hour at that moment versus $0.5292/hour on demand. That reduces the
Ethereum worker by roughly 60% while it runs, but Spot price and availability
are dynamic, and no Spot bill has been observed over a sustained period.

Scaling only Ethereum capacity to zero leaves roughly $0.347/hour—about
$253/month at 730 hours—before storage, RDS, logs, and traffic. It is a warm
pause, not zero cost, and it is the lab's observed state right now. The
replacement also right-sized a running three-node fleet's roots from 260 GiB to
110 GiB, which at the then-current $0.08/GiB-month gp3 rate takes their nominal
storage from $20.80 to $8.80 per month; while Ethereum capacity is paused, only
the two 40-GiB system roots exist. Recalculate before every sustained run; see
[EKS pricing](https://aws.amazon.com/eks/pricing/),
[VPC pricing](https://aws.amazon.com/vpc/pricing/), and
[EBS pricing](https://aws.amazon.com/ebs/pricing/).

The lab defaults to one NAT gateway for cost. A production-shaped environment sets `single_nat_gateway = false`, isolates additional signing/data tiers, and uses a private runner or private control-plane connectivity.

Terraform creates the Secrets Manager object for the Engine API JWT but not its
value. A separate restricted operator bootstrap must generate and write that
value directly to Secrets Manager without placing it in Terraform state, Git,
shell history, or workflow logs. That bootstrap is not implemented yet, so the
secret container is declared infrastructure rather than runtime readiness.
