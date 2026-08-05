# EKS development-cluster entrypoint

This directory is the Flux reconciliation root for the single EKS `dev`
environment. It is bootstrapped on the live cluster. Follow
[`docs/runbooks/eks-flux-bootstrap.md`](../../docs/runbooks/eks-flux-bootstrap.md)
from a trusted workstation; GitHub Actions has no cluster or AWS deployment
credential.

The dependency chain is:

```text
infrastructure-controllers
          |
          v
infrastructure-configs
          |
          v
signer-infrastructure-configs
          |
          v
signer-prerequisites
          |
          v
        apps
          |
          v
      node-apps
```

Controllers, common Engine-JWT/storage configuration, the node branch, signer
infrastructure, database prerequisites, and the shared signer reconcile
continuously. The RDS credential, TLS path, Flyway migration, migration branch
ENI, signer branch ENI, generation-pinned network config, and one encrypted
validator key were qualified before validator activation. `node-apps` depends
on the Ready `apps` layer so the validator client cannot precede Web3Signer.

The three scoped reader-role ARNs are non-secret Terraform outputs, but they are
account-specific and therefore are not committed here. The trusted local
bootstrap initially creates `flux-system/aws-secret-store-role-arns` with only
the reviewed engine-reader field. The common `infrastructure-configs`
Kustomization references only that field and therefore cannot be blocked by a
missing database/signing role or Pod security-group ID. The ConfigMap is annotated
`kustomize.toolkit.fluxcd.io/prune=disabled` because it is a bootstrap input,
not a Git-managed application object.

The controller Kustomization also requires the separate
`flux-system/aws-ingress-inputs` ConfigMap containing the cluster name, AWS
region, VPC ID, and exact-hostname ACM certificate ARN from the two Terraform
roots. These values are non-secret but environment-specific, use the same
prune-disabled bootstrap-input boundary, and must exist before ingress-nginx
or AWS Load Balancer Controller reconciliation.

Before the `signer-infrastructure-configs` layer is admitted, the operator adds
the database/signing reader fields and distinct
non-secret
`WEB3SIGNER_POD_SECURITY_GROUP_ID` and
`WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID` values used by exact-selector AWS
VPC CNI `SecurityGroupPolicy` objects; both must match Terraform outputs and
neither has a node-security-group fallback. Missing signer fields therefore
fail the signer branch closed without preventing node-only sync. Every reader
value must be its corresponding scoped role ARN, never the base Pod Identity
role.

The EKS controller overlay installs Prometheus/Grafana and the AWS Load
Balancer Controller but deliberately excludes
the local Loki/Alloy topology and local-only dashboard ConfigMaps. Pair and
signer metrics still retain the EKS `cluster`/`environment` label contract; an
AWS logging/RDS-dashboard adapter must land separately rather than presenting
local CloudNativePG and Loki claims as EKS evidence.

`flux-system/kustomization.yaml` reuses the repository's generated, pinned Flux
v2.8.8 controller bundle and patches only the sync path from `clusters/local`
to `clusters/dev`. The trusted-local runbook creates a read-only deploy key and
applies that already-reviewed desired state; bootstrap does not push directly
to the branch-protected `main` branch.
