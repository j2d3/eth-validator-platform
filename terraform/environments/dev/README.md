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
runtime evidence for the foundation described below; it is not evidence for RDS,
the declared-but-unbootstrapped Flux/External Secrets/application-storage
adapters, P2P, or validator paths.

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
| Add-ons and identity | VPC CNI, CoreDNS, kube-proxy, EKS Pod Identity agent, EBS CSI with a dedicated role | All five managed add-ons and their pods were Running; EBS CSI controller/node pods were fully Ready with zero restarts. The application `gp3` StorageClass was accepted by a server-side dry-run against this cluster, but it was not persisted, and no application PVC or EBS volume exists | Trusted-local bootstrap and runtime qualification of the declared Flux EKS overlay, AWS Secrets Manager `ClusterSecretStore` interfaces, and registered application StorageClass in [`clusters/dev`](../../../clusters/dev/README.md) |
| Secrets | Empty Secrets Manager containers for the EL/CL Engine JWT, Web3Signer database application credential, and encrypted signing-key bundle; External Secrets Pod Identity may assume only secret-scoped reader roles | The Engine JWT container and original Pod Identity association exist; no secret value was created. The signing containers and role split in this branch are declared source only until a reviewed apply | Restricted operator bootstrap, AWS `SecretStore` adapters, signing-key import/rotation evidence |
| Slashing database | Private Single-AZ RDS PostgreSQL 18; isolated database subnets; separate signer-Pod and schema-migration-Pod security-group paths; encrypted 20-GiB gp3 with a 100-GiB ceiling; seven-day PITR; deletion protection and final snapshot by default | None — no RDS, database subnets, workload Pod security groups, application database user, or application credential exists yet | Reviewed plan/apply, credential bootstrap, EKS adapters, migrations, backup/restore, outage, reconnection, and later Multi-AZ qualification |
| Encryption | AWS-managed encryption is requested for node root volumes and Terraform state | Remote state controls were applied; every observed node root was encrypted gp3 at baseline 3,000 IOPS / 125 MiB/s, and after the replacement they measured 40 GiB per system node and 30 GiB per Ethereum node | Explicit KMS/AWS-managed-key decision for RDS, EBS application data, and Secrets Manager |
| Public Ethereum networking | Generation-pinned Ephemery chart values declare one public, P2P-only NLB with no fixed node ports; Engine API, JSON-RPC, metrics, and signer endpoints remain internal | None | Flux/NLB reconciliation plus TCP, UDP discovery, and advertised-address qualification |

Local CloudNativePG, local-path volumes, and the Kubernetes External Secrets
provider are contract-compatible development adapters; they are not evidence
for RDS, EBS, IAM, KMS, or EKS behavior.

## Web3Signer AWS data and secret boundary

This root now declares the AWS half of the shared signer contract without
claiming that the contract is live. The database is an RDS PostgreSQL instance
outside EKS. It is not publicly accessible and its database subnets have their
own route table with neither an internet-gateway nor NAT route. The subnet group
spans all three selected Availability Zones so the topology can evolve later,
but the first lab instance is intentionally Single-AZ and defaults to the first
AZ.

The initial cost profile is deliberately small: `db.t4g.micro`, 20 GiB of
encrypted `gp3`, storage autoscaling capped at 100 GiB, no Performance Insights,
no enhanced monitoring, and seven-day PostgreSQL log retention. A read-only AWS
capability query on 2026-08-02 confirmed that PostgreSQL 18.4 was available in
`us-west-2` and that `db.t4g.micro` supported `gp3` from 20 GiB. Terraform pins
the PostgreSQL **major** (`18`), enables compatible automatic minor upgrades,
and forbids automatic major upgrades. Availability and pricing must be checked
again before each apply; the observation is evidence, not a future guarantee.

### Network admission fails closed

RDS port 5432 accepts exactly two distinct workload identities: the long-lived
`web3signer_pod` group and the short-lived `web3signer_migration_pod` group. The
EKS node security group is not admitted to the database: doing so would make
every Pod colocated on a system node a potential network client. The migration
group has only PostgreSQL and cluster-DNS egress; it does not inherit the
signer's API/metrics ingress surface.

The VPC CNI is declared with Security Groups for Pods and native NetworkPolicy
enforcement enabled in `standard` mode, and the EKS cluster role receives only
the AWS-managed resource-controller policy required for branch ENIs. Standard
mode is the AWS-recommended compatibility mode when Pod security groups and
NetworkPolicy are combined; signing admission remains a later, explicit gate.
Before apply, resolve the installed VPC CNI version and verify every configured
key against `aws eks describe-addon-configuration`. Enabling Pod ENIs or changing
enforcing mode affects newly launched Pods; after the stopped-workload gate,
recycle affected Pods and nodes and observe trunk/branch ENI attachment before
crediting either security-group path as runtime evidence.

Terraform outputs both workload security-group IDs; it does **not** attach
either group. Later Flux-owned `SecurityGroupPolicy` resources must select the
Web3Signer Pod in `signing` and the schema-migration Job in `database`
independently. Until those adapters reconcile, neither principal can satisfy
its RDS ingress rule. The AWS overlay must also patch each base NetworkPolicy's
local CloudNativePG selector to allow PostgreSQL within the VPC CIDR. Both
layers are required: Kubernetes NetworkPolicy selects each workload, while RDS
admits only its distinct branch-ENI identity.

The trusted Flux bootstrap maps the scalar outputs
`web3signer_pod_security_group_id` and
`web3signer_migration_pod_security_group_id` to ConfigMap keys
`WEB3SIGNER_POD_SECURITY_GROUP_ID` and
`WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID` respectively. Those identifiers
are network references, not credentials.

### Secret and IAM flow

Terraform creates secret **containers**, never secret versions:

- `.../ethereum/engine-jwt` for the pair-private Engine API JWT;
- `.../signing/web3signer-database` for the restricted application connection
  JSON (`host`, `port`, `database`, `username`, `password`); the EKS adapter
  projects the canonical `database` property to target `dbname` only where a
  Flyway/Web3Signer environment requires that alias; and
- `.../signing/validator-keystore` for the first encrypted testnet keystore
  bundle and its password.

RDS generates its master password and stores it in an RDS-managed Secrets
Manager secret. Terraform state receives the secret ARN, not the password. The
trusted-local `hack/bootstrap-web3signer-database.py` procedure uses that master
identity in process memory to create a least-privilege `web3signer` application
role and write the application connection JSON directly to its empty container.
Validator onboarding separately writes encrypted keystore material directly to
its container. Neither procedure may pass a secret through a Terraform
variable, plan, output value, shell argument, GitHub log, or Git.

External Secrets has one Pod Identity base role with only the
`sts:AssumeRole` and `sts:TagSession` actions needed to establish sessions on
the scoped reader roles. EKS Pod Identity marks its workload session tags as
transitive, so role chaining requires both actions on the base policy and each
target role's trust policy. Environment-specific AWS `SecretStore` resources
later select one of three target roles:

- the engine reader can read only the Engine JWT; and
- the database reader can read only the Web3Signer application credential; and
- the signing reader can read only the encrypted validator-keystore bundle.

The `external_secrets_reader_role_arns` output exposes map fields `engine`,
`database`, and `signing`. The trusted Flux bootstrap maps them to
`EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN`,
`EXTERNAL_SECRETS_DATABASE_READER_ROLE_ARN`, and
`EXTERNAL_SECRETS_SIGNING_READER_ROLE_ARN`; a database store must never use the
signing-key reader as a convenience alias.

Web3Signer receives no Pod Identity association and no AWS API permission. It
sees only Kubernetes Secrets materialized by External Secrets. The database
credential may be projected into `database` and `signing` for the migration and
signer consumers; the validator-keystore reader remains a separate signing-key
boundary. The RDS master secret is granted to none of the reader roles.

### Runtime gates still outstanding

Do not describe the EKS signer path as ready until a reviewed plan/apply and all
of these gates have evidence:

1. the private RDS endpoint resolves only inside the VPC and rejects the EKS
   node security group;
2. separate `SecurityGroupPolicy` resources attach the signer group only to
   Web3Signer and the migration group only to the Flyway Job; branch ENIs are
   observed after the VPC CNI/node recycle, and DNS, probes, signer API,
   metrics, and PostgreSQL work under the combined Pod-SG and NetworkPolicy
   controls;
3. restricted bootstrap creates a non-master application role and writes the
   expected `host`, `port`, `database`, `username`, and `password` properties
   without leaking values;
4. AWS `SecretStore`/`ExternalSecret` resources assume the distinct engine,
   database, and signing reader roles and project only each role's intended
   material;
5. the Flyway Job carries the migration Pod group and applies the exact
   migrations shipped with the pinned Web3Signer image over
   certificate-verified TLS, then Web3Signer becomes ready with zero keys;
6. backup/PITR restore proves row continuity and a conflicting-signature test is
   safely rejected before any funded identity is admitted; and
7. RDS outage and reconnect exercises leave signing failed closed.

### Pause, teardown, and cost semantics

Scaling Ethereum workers to zero does not stop RDS, the EKS control plane,
system nodes, NAT, Secrets Manager, CloudWatch logs, database storage, or backup
storage. RDS may be stopped manually for a short lab pause, but AWS restarts a
stopped DB instance after at most seven days. A stop therefore reduces instance
hours temporarily; it is not a durable zero-cost state, and Terraform does not
own the transient stopped/running state.

Deletion protection is on by default. A destroy requires a separate reviewed
change disabling it, and the default path requires a final snapshot while
retaining automated backups for their retention window. The fixed final
snapshot identifier must be checked for collision before a later destroy. An
extended pause may eventually use snapshot → delete → restore, but that is not a
safe cost control until the restore drill above is proven; a snapshot that has
not passed Web3Signer continuity and rejection tests is not a signing authority.
Secret recovery windows are seven days for the replaceable database-connection
container and 30 days for the signing-key container.

For a production-shaped evolution, enable Multi-AZ, independently size and load
test the database, use customer-managed KMS keys with reviewed key policies,
add database/event/storage/connection alarms, verify certificate hostname and
chain (`sslmode=verify-full` plus the RDS CA bundle), exercise credential
rotation, and partition signer/database cells by explicit failure-domain and
economic-exposure policy. None of those claims is implied by this Single-AZ
testnet declaration.

Primary implementation contracts:

- [Security Groups for Pods and NetworkPolicy][eks-sgpp]
- [Temporary RDS stop/start behavior][rds-stop]
- [RDS automated backups and point-in-time recovery][rds-backups]
- [External Secrets AWS authentication and role assumption][eso-aws]

[eks-sgpp]: https://docs.aws.amazon.com/eks/latest/best-practices/sgpp.html
[rds-stop]: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html
[rds-backups]: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.html
[eso-aws]: https://external-secrets.io/latest/provider/aws-access/

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
