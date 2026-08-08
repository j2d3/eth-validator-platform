# EKS cold-standby lifecycle

## Purpose

The lab has two different pause modes:

| Mode | What remains | Typical cost | Recovery |
|---|---|---:|---|
| Warm pause | EKS, system nodes, RDS, NAT, retained EBS, and durable secrets | roughly $10–15/day | resume workers and Flux |
| Cold standby | S3/Terraform state, RDS recovery data, Secrets Manager, DNS, and image repositories | low single-digit dollars/month plus backup/storage | recreate the AWS foundation and resync clients |

Cold standby is a rebuildable state, not a stopped Kubernetes cluster. It is
intended for multi-day or multi-week pauses when preserving the ability to
recreate the environment matters more than preserving already-synced chain
data.

This runbook defines the contract. It does not authorize a destroy operation by
itself. Destruction requires a separate reviewed change and an operator's
explicit confirmation after every preflight gate passes.

## Durable state classification

### Keep in AWS

- Terraform state in the protected, versioned bootstrap S3 bucket.
- An encrypted RDS final snapshot (or an RDS automated-backup recovery point)
  containing the Web3Signer PostgreSQL slashing-protection database.
- Encrypted validator keystores and passwords in identity-addressed Secrets
  Manager containers. These are never copied into S3, Git, Terraform state, or
  a backup archive.
- The Engine JWT in Secrets Manager. It may be regenerated on rebuild, but
  retaining it avoids unnecessary EL/CL reconfiguration.
- A small, non-secret manifest in S3 containing snapshot identifiers, Terraform
  output metadata, Git revision, and the recovery verification timestamp.
- DNS, certificates, the portal hosting configuration, and ECR repositories if
  their low fixed cost is acceptable.

### Recreate or resync

- EKS control plane, VPC, subnets, NAT, security groups, IAM roles, managed
  node-group definitions, add-ons, and load balancers.
- Ethereum worker nodes and their disposable root disks.
- Ethereum chain-data EBS volumes by default. Re-syncing is cheaper and less
  operationally ambiguous than maintaining a large set of EBS snapshots. An
  optional later mode can snapshot selected chain volumes when startup time is
  more important than storage cost.
- Flux controllers and all in-cluster workloads. Git remains their source of
  truth and bootstrap replays the committed revision.

### Why S3 is not the slashing database

An S3 export of PostgreSQL rows is not a drop-in Web3Signer database. A restore
must preserve schema, constraints, indexes, and every signing-history row. The
canonical recovery artifact is therefore an encrypted RDS snapshot or an
isolated PostgreSQL backup with a tested restore procedure. S3 may hold a
manifest or an additional encrypted export, but signing must not resume merely
because an object was uploaded.

## Required teardown sequence

1. Merge a GitOps stop for every assignment and wait until no validator client,
   execution client, or consensus client Pod remains.
2. Verify signing is disabled, no key is admitted to Web3Signer, and the RDS
   slashing database is healthy.
3. Capture and verify an RDS final snapshot. Record its identifier and source
   database resource ID in the recovery manifest.
4. Verify the snapshot is encrypted, the expected database/schema inventory is
   present, and the snapshot is visible from the recovery account/region.
5. Verify the Terraform backend is reachable and versioned. Upload only the
   non-secret recovery manifest to its dedicated S3 prefix.
6. Preserve the Secrets Manager containers and their recovery policies. Do not
   delete and recreate signing-key secrets as part of a normal cold standby.
7. Apply the reviewed cold-standby Terraform mode, which removes EKS, VPC,
   NAT, RDS, load balancers, and disposable volumes in dependency order.
8. Verify that only the explicitly retained resources remain and record the
   final AWS cost-bearing inventory.

The teardown must refuse to proceed when any signing flag is true, when any
active workload exists, when the RDS snapshot is absent/unverified, or when the
secret containers cannot be identified as retained resources.

## Required recovery sequence

1. Read the recovery manifest from S3 and verify its checksum and Git revision.
2. Re-run Terraform for the same environment with a reviewed RDS restore
   identifier. The restore must explicitly set the private subnet group,
   security group, parameter group, encryption, deletion protection, and backup
   policy; RDS defaults are not sufficient.
3. Wait for RDS availability and run the slashing schema/row-continuity checks
   before projecting any signing key.
4. Recreate EKS and its add-ons, then bootstrap Flux from the recorded Git
   revision. Flux must initially reconcile with all assignments stopped and
   signing disabled.
5. Recreate worker capacity, allow chain-data PVCs to bind, and resync one
   client pair at a time. Do not restore a validator duty merely because Pods
   are Ready.
6. Reconcile the Engine JWT and database Secrets through External Secrets.
   Reconcile signing-key Secrets only after the restored RDS path and identity
   inventory match the manifest.
7. Re-run the normal activation gates: chain identity, sync distance,
   doppelganger protection, exact public-key match, slashing backup evidence,
   and one-active-assignment uniqueness.

## Terraform shape required

The implementation should separate durable resources from ephemeral foundation
resources instead of relying on a broad `terraform destroy`:

- a durable root for the protected S3 backend and retained Secrets Manager
  containers;
- an ephemeral root for VPC, EKS, NAT, load balancers, and RDS; and
- an explicit `cold_restore_snapshot_identifier` input for restoring RDS from
  the verified snapshot.

The current development root combines several of these concerns, so the first
implementation step is a refactor and plan-only verification. No cold teardown
should be attempted until a destroy plan proves that secret containers and the
Terraform backend are outside its target set.

## Cost expectation

Cold standby removes the largest hourly charges: EKS control plane, system
nodes, NAT gateway, RDS instance hours, load balancers, and running workers.
Remaining charges are expected to be primarily Secrets Manager, KMS, S3,
Route 53, ECR, and retained encrypted backup storage. Actual pricing must be
read from Cost Explorer after the first complete cold-standby day.

