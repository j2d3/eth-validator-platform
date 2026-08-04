# EKS development-cluster entrypoint

This directory is the Flux reconciliation root for the single EKS `dev`
environment. It is committed but has not been bootstrapped onto the live
cluster. Follow
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
 signer-prerequisites  (committed suspended)
          |
          v
        apps            (committed suspended)
```

Controllers and AWS interface configuration may reconcile without launching an
Ethereum client. The signer migration and application layers remain suspended
until the RDS credential, network, TLS, backup, and migration gates in the
runbook are proven. The later RDS slice must supply or prove the AWS RDS CA
trust path; this slice only commits the fail-closed `sslmode=verify-full`
requirement. Removing either suspension is deployment authorization and
must be a separate reviewed Git change.

The three scoped reader-role ARNs are non-secret Terraform outputs, but
they are account-specific and therefore are not committed here. The trusted
local bootstrap creates `flux-system/aws-secret-store-role-arns` from the
reviewed `external_secrets_reader_role_arns` output. The `infrastructure-configs`
Kustomization refuses to reconcile until that ConfigMap exists; it is annotated
`kustomize.toolkit.fluxcd.io/prune=disabled` because it is a bootstrap input,
not a Git-managed application object. The engine, database, and signing-key
values must be their corresponding scoped reader-role ARNs, never the base Pod
Identity role. The same bootstrap ConfigMap supplies distinct non-secret
`WEB3SIGNER_POD_SECURITY_GROUP_ID` and
`WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID` values used by exact-selector AWS
VPC CNI `SecurityGroupPolicy` objects; both must match Terraform outputs and
neither has a node-security-group fallback.

The EKS controller overlay installs Prometheus/Grafana but deliberately excludes
the local Loki/Alloy topology and local-only dashboard ConfigMaps. Pair and
signer metrics still retain the EKS `cluster`/`environment` label contract; an
AWS logging/RDS-dashboard adapter must land separately rather than presenting
local CloudNativePG and Loki claims as EKS evidence.

`flux-system/kustomization.yaml` reuses the repository's generated, pinned Flux
v2.8.8 controller bundle and patches only the sync path from `clusters/local`
to `clusters/dev`. The trusted-local runbook creates a read-only deploy key and
applies that already-reviewed desired state; bootstrap does not push directly
to the branch-protected `main` branch.
