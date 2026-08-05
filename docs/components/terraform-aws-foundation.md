# Terraform AWS foundation

**Owner**: `terraform/environments/dev/` and `terraform/environments/dns/`,
applied from a trusted operator workstation.

## Scope

Terraform owns the infrequent, account-level AWS state:

- **VPC** with private + public + intra subnets across three AZs in
  `us-west-2`, route tables, one NAT gateway, one Internet gateway, VPC
  flow logs to CloudWatch.
- **EKS 1.35** cluster (control-plane logging enabled).
- **Managed node groups** — see
  [`eks-capacity-and-storage`](eks-capacity-and-storage.md) for the
  four-group topology and instance-type inventory.
- **IAM roles + EKS Pod Identity Associations** for two workload
  identities today: the EBS CSI controller and the External Secrets
  Operator. See "IAM and Pod Identity" below for exactly what does and
  does not have Pod Identity today.
- **RDS `db.t4g.micro`** (pinned engine version) Single-AZ,
  `storage_encrypted = true` (AWS-managed key), TLS required
  (`rds.force_ssl=1`), backups retained.
- **AWS Secrets Manager containers** (empty by construction) — one per
  identity-addressed key via `for_each`, plus the engine-JWT container
  and the Web3Signer database credential container.
- **ACM certificates + Route 53 DNS records** (in the separate `dns`
  root).
- **Amazon EBS CSI prerequisites** and StorageClass rewire.
- **Security Groups**: pod-scoped SGs for Web3Signer, the Web3Signer
  migration Job, and the Web3Signer RDS boundary; a security-group
  ingress rule allowing only those pod-scoped SGs to reach RDS on
  5432.

## Explicitly not owned

- Any HelmRelease, Kubernetes object, ConfigMap, or manifest under
  `platform/`.
- Any secret **value** inside a Secrets Manager container.
- Any validator identity, deposit, or in-cluster application state.
- Any GitHub Actions workflow.

## The EKS design choices

### Why EKS and not self-managed Kubernetes on EC2

- **Blast-radius control on the control plane.** EKS runs the etcd +
  API server + scheduler on AWS-managed infrastructure. Losing a
  worker node cannot corrupt cluster state.
- **Managed EKS Pod Identity.** In-cluster workloads that ESO uses
  exchange Kubernetes ServiceAccounts for scoped IAM credentials
  without long-lived keys — see below for scope.
- **Managed control-plane patching.** Kubernetes CVE cycles have been
  fast in the last year; getting the control plane out of the
  operator's patch queue is worth the per-cluster cost.
- **AWS-native VPC CNI.** Pods get real VPC IPs, which makes
  Security Groups for Pods (below) work and makes VPC flow logs
  meaningful for in-cluster traffic.

### Segmentation — namespace layout

The cluster's namespaces are deliberately narrow and single-purpose:

| Namespace | Contents | Cross-namespace surface |
|---|---|---|
| `flux-system` | Flux controllers | Reads Git; writes across namespaces |
| `external-secrets` | ESO controllers | Assumes reader IAM roles; writes ExternalSecret targets in tenant namespaces |
| `cert-manager` | cert-manager controllers | Solves ACM DNS-01; writes TLS Secrets |
| `monitoring` | kube-prometheus-stack, node-exporter | Scrapes cluster-wide (RBAC-scoped) |
| `signing` | Web3Signer Deployment, shared validator-keystore ExternalSecret | Only namespace with access to keystore Secrets |
| `database` | Flyway migration Job | Only namespace whose Pods carry the Web3Signer-migration Pod SG |
| `ethereum` | Client pair HelmReleases (EL + CL StatefulSets) | Talks to `signing` on Web3Signer's HTTP port only |
| `portal` | Public status API + reader-facing Grafana | Read-only ingress; no cluster mutation |

**The `signing` namespace is the security boundary for key material.**
Nothing outside `signing` can read the projected keystore Secret. The
rest of the cluster interacts with the signer through a single
Kubernetes Service that fronts the Web3Signer HTTP API.

### Segmentation — Security Groups for Pods

For Web3Signer and the Flyway migration Job, the VPC CNI attaches an
**ENI-per-Pod** with dedicated Security Groups
(`aws_security_group.web3signer_pod` and
`aws_security_group.web3signer_migration_pod`). Those SGs are the only
SGs allowed to reach `aws_security_group.web3signer_database` on 5432
(via `aws_vpc_security_group_ingress_rule.database_from_web3signer` and
`.database_from_web3signer_migration`). Egress from those two pod SGs
to the database SG is symmetrically constrained. This is defense-in-
depth beyond namespace boundaries: reaching RDS on 5432 from any other
Pod in the cluster is blocked at the AWS VPC network layer even if
NetworkPolicy is misconfigured or CNI enforcement is bypassed.

### IAM and Pod Identity (as implemented today)

Only the following workload identities have EKS Pod Identity
Associations at the current head:

- **EBS CSI controller** — has a Pod Identity Association from the
  managed add-on, attached to a role with the
  `AmazonEBSCSIDriverPolicy` managed policy.
- **External Secrets Operator** — has a Pod Identity Association to
  `aws_iam_role.external_secrets`, whose only permission is
  `sts:AssumeRole` + `sts:TagSession` on three per-purpose reader
  roles:
  - `external_secrets_engine_reader` — `GetSecretValue` on the engine-JWT
    container ARN only.
  - `external_secrets_database_reader` — `GetSecretValue` on the
    Web3Signer database credential container ARN only.
  - `external_secrets_signing_reader` — `GetSecretValue` on the exact
    list of validator-keystore container ARNs (via `[for signing_key
    in values(...) : signing_key.arn]` — enumerates, no wildcard).
- **Web3Signer itself** does **not** have Pod Identity. It reads
  keystore material only through the ExternalSecret-materialized
  Kubernetes Secret; it never calls AWS APIs directly. A contract
  test asserts the absence of a Web3Signer Pod Identity association
  to guard against accidentally widening this surface.

There are no long-lived AWS access keys on the operator laptop
(SSO-issued short-lived credentials) or in CI.

### Encryption (as implemented today, vs future work)

Currently implemented:

- **EBS** volumes: `encrypted = true` in the launch template — this
  uses the **AWS-managed** default EBS key.
- **RDS**: `storage_encrypted = true` — this uses the **AWS-managed**
  default RDS key. `rds.force_ssl = 1` requires TLS in transit;
  clients pass `sslmode=verify-full` with a pinned regional CA (see
  [`web3signer-and-slashing-protection`](web3signer-and-slashing-protection.md)).
- **Secrets Manager** containers: encrypted with the account's
  AWS-managed Secrets Manager key.

**Not implemented today, honest gap list:**

- No `aws_kms_key` resources declared in this root.
- No **customer-managed KMS keys** for EBS, RDS, or Secrets Manager.
- No **EKS Secrets encryption config** (Kubernetes-side etcd envelope
  encryption for the `Secret` object is default AWS-managed).
- No **RDS IAM database authentication** — the connection is
  password-authenticated via a Terraform-declared Secrets-Manager-
  backed credential.

Each of the above is a reasonable next hardening step, particularly
before this shape would go to production, and each is a discrete
Terraform change. They are called out here so the doc does not
overclaim.

### The tradeoffs that hurt

- **Single-AZ RDS.** Halves availability during an AZ event. Chosen
  because Multi-AZ doubles RDS cost and this lab is not a production
  commitment. Production replacement would flip to Multi-AZ.
- **One NAT gateway.** Multi-AZ NAT is the AWS best practice; one NAT
  is an operating-cost savings and an AZ-outage risk.
- **Public EKS API endpoint** with CIDR allowlist to the operator
  workstation. Fully-private endpoint would require a bastion or SSM
  session-manager setup that the lab hasn't justified.
- **No Falco / GuardDuty EKS Protection.** Runtime intrusion
  detection is future work.

## Boundary enforcement

- **No CI apply/destroy**: `terraform apply` runs from a trusted
  operator workstation. There is intentionally no GitHub Actions
  Terraform apply workflow.
- **State separation**: `dev` and `dns` environments have separate
  remote state files in the encrypted S3 backend.
- **`moved` blocks preserve populated state**: converting a resource
  to `for_each` uses `moved { from = ... to = ...[key] }` so live
  containers (like validator #1's keystore) are not destroyed by
  state re-addressing (see PR #127).
- **Targeted plans**: signing-lane changes cite the exact `N add / M
  in-place update / 0 destroy` shape in review comments.

## References

- Development root README: `terraform/environments/dev/README.md`
- Runbook: [`eks-flux-bootstrap`](../runbooks/eks-flux-bootstrap.md)
- Capacity + storage: [`eks-capacity-and-storage`](eks-capacity-and-storage.md)
- Secrets: [`secrets-and-key-projection`](secrets-and-key-projection.md)
- Signing: [`web3signer-and-slashing-protection`](web3signer-and-slashing-protection.md)
