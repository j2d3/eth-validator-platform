# Bootstrap Flux onto the EKS development cluster

## Purpose and current state

This runbook connects the existing private GitHub repository to the single EKS
`dev` cluster from a trusted workstation. Terraform owns the AWS foundation;
Flux becomes the only continuous writer of in-cluster platform state.

The repository currently declares this chain:

```text
infrastructure-controllers
          |
          v
infrastructure-configs
          +--------------------------+
          |                          |
          v                          v
      node-apps          signer-infrastructure-configs
       (suspended)                 (suspended)
                                      |
                                      v
                           signer-prerequisites
                                (suspended)
                                      |
                                      v
                                    apps
                                (suspended)
```

This declaration is not runtime evidence. At publication time Flux has not been
bootstrapped onto EKS, the gp3 StorageClass has not been persisted, and none of
the four downstream suspensions has been removed.

## Ownership boundary

| Concern | Owner | This runbook does |
|---|---|---|
| VPC, EKS, node groups, add-ons, Pod Identity association, secret containers | Trusted-local Terraform | Reads outputs and verifies prerequisites; never applies Terraform |
| Flux controllers and private-repository deploy key | Trusted-local bootstrap operator | Creates them once with the explicit command below |
| Kubernetes controllers, configuration, signer, dashboards, pair releases | Flux | Reconciles reviewed Git state only |
| Lifecycle request preparation | GitHub Actions | Opens reviewed Git changes; has no AWS credential, kubeconfig, or cluster writer |
| Secret values | Restricted operator path to AWS Secrets Manager | Verifies only object/property presence; never prints values |
| EKS adapter inputs | Trusted-local Terraform outputs -> `flux-system/aws-secret-store-role-arns` ConfigMap | Initially supplies only the non-secret engine reader ARN for common/node reconciliation; signer reader ARNs and distinct Web3Signer runtime/migration Pod security-group IDs are added only before the signer-only layer is resumed |

## 1. Preflight without mutation

Start from a clean, current `main` checkout and verify every identity before a
command capable of mutation:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git status --short
git config --local user.email

export PATH="$PWD/.local/bin:$PATH"
export GH_CONFIG_DIR="$HOME/.config/gh-j2d3"
gh api user --jq .login

export AWS_PROFILE=default
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
```

Expected: the worktree is clean, the project-local Flux and Terraform binaries
are first on `PATH`, `gh` reports `j2d3`, and the AWS identity is the same
account that owns the Terraform `dev` state. Do not paste the account number or
ARN into Git, issues, or logs.

Verify the committed safety posture before pointing Flux at the path:

```bash
make check
kubectl kustomize clusters/dev >/dev/null
kubectl kustomize platform/infrastructure/overlays/dev/controllers >/dev/null
kubectl kustomize platform/infrastructure/configs/dev >/dev/null
kubectl kustomize platform/infrastructure/configs/dev/signer >/dev/null
kubectl kustomize platform/apps/prerequisites/dev >/dev/null
kubectl kustomize platform/apps/dev >/dev/null
kubectl kustomize platform/apps/nodes/dev >/dev/null
```

`clusters/dev/node-apps.yaml`, `signer-infrastructure-configs.yaml`,
`signer-prerequisites.yaml`, and `apps.yaml` must all contain `suspend: true`.
The rendered assignment must contain
`lifecycleState: stopped`, `validator.enabled: false`, and
`slashingProtectionConfirmed: false`.

## 2. Select the EKS context from Terraform outputs

Terraform exposes names and endpoints, not credentials:

```bash
TF_ROOT=terraform/environments/dev
AWS_REGION="$(terraform -chdir="$TF_ROOT" output -raw aws_region)"
CLUSTER_NAME="$(terraform -chdir="$TF_ROOT" output -raw cluster_name)"

aws eks update-kubeconfig \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  --alias eth-validator-platform-dev

kubectl config current-context
kubectl get nodes -L workload,topology.kubernetes.io/zone
flux check --pre
```

Stop if the context is not `eth-validator-platform-dev`, the two system nodes
are not Ready, or the Flux preflight is incompatible with the pinned release.

The RDS security group is the only enforced network boundary until the VPC CNI
network-policy feature is enabled. Security groups for Pods are also inert until
Pod ENI support is enabled. Before treating either Kubernetes `NetworkPolicy` or
a `SecurityGroupPolicy` as evidence, verify all four Terraform-managed add-on
settings:

```bash
VPC_CNI_CONFIG="$(aws eks describe-addon \
  --cluster-name "$CLUSTER_NAME" \
  --addon-name vpc-cni \
  --query configurationValues --output text)"
jq -e '
  .enableNetworkPolicy == "true" and
  .env.ENABLE_POD_ENI == "true" and
  .env.NETWORK_POLICY_ENFORCING_MODE == "standard" and
  .env.POD_SECURITY_GROUP_ENFORCING_MODE == "standard"
' <<<"$VPC_CNI_CONFIG" >/dev/null

kubectl -n kube-system rollout status daemonset/aws-node --timeout=5m
kubectl get nodes -l workload=system -o json | jq -e '
  (.items | length) > 0 and
  all(.items[];
    ((.status.allocatable["vpc.amazonaws.com/pod-eni"] // "0") | tonumber) > 0
  )
' >/dev/null
```

If that check fails, stop. Do not unsuspend a node or signer layer; update the
reviewed Terraform/add-on contract first. These configuration and trunk-capacity
checks are prerequisites, not runtime proof that either policy engine enforced a
decision. Step 5 uses a matched allow/deny test against one policy-only target;
Steps 6 and 7 separately prove that the selected Pods received branch ENIs with
the exact Terraform security groups.

## 3. Prove the AWS adapter inputs

The EKS manifests contain references, never secret values:

| Interface | Required contract |
|---|---|
| Engine JWT | Secrets Manager object `eth-validator-platform-dev/ethereum/engine-jwt`, JSON property `jwt.hex`; Terraform creates only the object |
| Web3Signer database | Secrets Manager object `eth-validator-platform-dev/signing/web3signer-database`, JSON properties `host`, `port`, `database`, `username`, `password`; the EKS adapters project `database` to `dbname` for Flyway and retain `database` for Web3Signer |
| External Secrets identity | Terraform associates the base `external_secrets_role_arn` with ServiceAccount `external-secrets`; its only target-role permissions are `sts:AssumeRole` and `sts:TagSession`, which EKS Pod Identity requires when its transitive workload tags cross the role chain. The non-secret `external_secrets_reader_role_arns` output supplies separate engine, database, and signing-key reader roles through the Flux substitution ConfigMap |
| RDS network | Private endpoint resolves inside the `dev` VPC; TCP/5432 is admitted by both the Kubernetes egress policy and the dedicated runtime-or-migration Pod security-group path |
| TLS | Both JDBC URLs require `sslmode=verify-full`; the RDS slice must also prove an AWS RDS CA trust path for both the Flyway and Web3Signer images (image trust store or an explicitly mounted CA bundle) before any signer suspension changes |

The coordinated Terraform foundation must make the base role assume-only and
create three scoped reader roles: the engine reader may read only the Engine
JWT, the database reader only the database credential, and the signing reader
only the encrypted signing-key bundle. The RDS slice must create the database
secret, grant only the database reader access to it, and declare the chosen AWS
RDS CA trust mechanism before the signer-prerequisite suspension is removed.
The database store is available in both `database` and `signing` because Flyway
and Web3Signer consume the same database credential; its AWS role still cannot
read a signing key. The signer-only layer declares a signing-key
`ClusterSecretStore`, but no ExternalSecret consumes it and no key is loaded by
this non-signing slice.
`sslmode=verify-full` in desired state is necessary but is not evidence that
either image trusts the selected RDS CA. Confirm metadata without reading
secret values:

```bash
aws secretsmanager describe-secret \
  --secret-id eth-validator-platform-dev/ethereum/engine-jwt \
  --query '{Name:Name,LastChangedDate:LastChangedDate}'

aws eks list-pod-identity-associations \
  --cluster-name "$CLUSTER_NAME" \
  --namespace external-secrets \
  --service-account external-secrets
```

Before Flux can reconcile the common `infrastructure-configs`, stage only the
non-secret engine-reader ARN from Terraform. Step 4 materializes it after the
Flux manifests create the `flux-system` namespace. Database/signing roles and
Pod security-group IDs are signer-only inputs and are deliberately absent at
this stage. This is an explicit reviewed bootstrap input: it is not a secret,
it is not committed to Git, and it is not the base Pod Identity role. The
command deliberately prints neither ARN nor any secret value:

```bash
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
READER_ROLES_JSON="$(terraform -chdir="$TF_ROOT" output -json external_secrets_reader_role_arns)"
ENGINE_READER_ROLE_ARN="$(jq -er '.engine' <<<"$READER_ROLES_JSON")"
case "$ENGINE_READER_ROLE_ARN" in
  "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${CLUSTER_NAME}-eso-engine-reader") ;;
  *) echo "engine reader role ARN is outside the selected AWS account" >&2; exit 1 ;;
esac
```

Do not substitute the base `external_secrets_role_arn` output.

`optional: false` on Flux `postBuild.substituteFrom` fails reconciliation when
the ConfigMap is absent. The EKS Flux overlay also enables
`StrictPostBuildSubstitutions=true`, so a missing signer-only key fails the
signer Kustomization before it can apply a partial store or security-group
policy. The common layer references only the engine variable, so those absent
signer keys cannot block node-only reconciliation. Substitution still does not
validate an ARN or security-group value; the later signer admission stage
compares every applied value to its in-memory Terraform output.

Do not use `get-secret-value` in a transcript or CI job. A restricted bootstrap
procedure must write a 32-byte Engine API JWT as JSON under `jwt.hex` without
placing it in Terraform state, Git, shell history, or workflow logs.

## 4. Bootstrap with a read-only deploy key

This is the first mutating step. It creates a **read-only** deploy key on the
repository, stores its private half only in the EKS `flux-system`
Secret, and applies the already-reviewed Flux v2.8.8 controller/sync bundle.
It does not generate or push a commit directly to branch-protected `main`.

Create the key in a short-lived directory. The cleanup trap removes only the
three explicit temporary paths it created:

```bash
umask 077
BOOTSTRAP_DIR="$(mktemp -d "${TMPDIR%/}/flux-dev.XXXXXX")"
DEPLOY_KEY="$BOOTSTRAP_DIR/identity"
SECRET_MANIFEST="$BOOTSTRAP_DIR/flux-system-secret.yaml"
trap 'rm -f -- "$DEPLOY_KEY" "$DEPLOY_KEY.pub" "$SECRET_MANIFEST"; rmdir "$BOOTSTRAP_DIR"' EXIT

ssh-keygen -q -t ecdsa -b 384 -N '' \
  -C flux-eth-validator-platform-dev \
  -f "$DEPLOY_KEY"

gh repo deploy-key add "$DEPLOY_KEY.pub" \
  --repo j2d3/eth-validator-platform \
  --title flux-eth-validator-platform-dev
```

Do not pass `--allow-write`; the `gh` command therefore creates a read-only key.
Install the controller CRDs first, then create the auth Secret through Flux's
SSH host-key discovery, then apply the complete EKS overlay:

```bash
kubectl apply -f clusters/local/flux-system/gotk-components.yaml
kubectl wait --for=condition=Established \
  crd/gitrepositories.source.toolkit.fluxcd.io \
  crd/kustomizations.kustomize.toolkit.fluxcd.io \
  crd/helmreleases.helm.toolkit.fluxcd.io \
  --timeout=2m

kubectl -n flux-system create configmap aws-secret-store-role-arns \
  --from-literal=EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN="$ENGINE_READER_ROLE_ARN" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n flux-system annotate configmap aws-secret-store-role-arns \
  kustomize.toolkit.fluxcd.io/prune=disabled --overwrite

kubectl -n flux-system get configmap aws-secret-store-role-arns \
  -o jsonpath='{.metadata.annotations.kustomize\.toolkit\.fluxcd\.io/prune}{"\n"}'
kubectl -n flux-system get configmap aws-secret-store-role-arns \
  -o json | jq -e '
    (.data | keys) == ["EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN"]
  ' >/dev/null

flux create secret git flux-system \
  --namespace=flux-system \
  --url=ssh://git@github.com/j2d3/eth-validator-platform \
  --private-key-file="$DEPLOY_KEY" \
  --export >"$SECRET_MANIFEST"
kubectl apply -f "$SECRET_MANIFEST"

kubectl apply -k clusters/dev/flux-system
flux reconcile source git flux-system

rm -f -- "$DEPLOY_KEY" "$DEPLOY_KEY.pub" "$SECRET_MANIFEST"
rmdir "$BOOTSTRAP_DIR"
trap - EXIT
```

The temporary Secret manifest contains the private key. The restrictive umask
protects it until the trap deletes it on shell exit. Never attach it to an
issue, commit it, or print it. After the Kubernetes Secret exists, the local
private-key files are removed immediately.

The ConfigMap checks above verify only its annotation and key names. Step 5
compares each applied value without printing it.

Verify the public deploy-key metadata and the committed sync path:

```bash
gh api repos/j2d3/eth-validator-platform/keys \
  --jq '.[] | select(.title == "flux-eth-validator-platform-dev") | {title,read_only}'
kubectl -n flux-system get gitrepository flux-system
kubectl -n flux-system get kustomization flux-system \
  -o jsonpath='{.spec.path}{"\n"}'
```

The key must report `read_only: true` and the sync path must be
`./clusters/dev`. If bootstrap fails after the GitHub key is created but before
the Kubernetes Secret is healthy, delete that named deploy key before retrying;
never reuse an untracked private key.

## 5. Verify the safe substrate

```bash
flux get sources git -A
flux get kustomizations -A
flux get helmreleases -A
kubectl get storageclass ebs-gp3-encrypted
kubectl get clustersecretstore aws-engine-secrets
kubectl get pods -n external-secrets
kubectl get pods -n observability
kubectl get pods,statefulsets,persistentvolumeclaims -n ethereum
```

Rehydrate the Terraform output variable from Step 3 if this is a new shell,
then compare the **applied** common adapter without printing it:

```bash
test "$(kubectl get clustersecretstore aws-engine-secrets -o jsonpath='{.spec.provider.aws.role}')" = \
  "$ENGINE_READER_ROLE_ARN"
```

Expected after the first reconciliation:

- `infrastructure-controllers` and `infrastructure-configs` are Ready;
- `node-apps`, `signer-infrastructure-configs`, `signer-prerequisites`, and
  `apps` report suspended;
- the encrypted gp3 class exists and is not the default;
- Prometheus and Grafana run, while the explicitly local Loki/Alloy and
  CloudNativePG dashboard surfaces remain absent pending an AWS adapter;
- no Ethereum Pod, StatefulSet, or bound chain-data PVC exists;
- no validator client or signing key exists.

The expected NetworkPolicy objects are only declarations until a matched
positive/negative probe proves enforcement. Do **not** use RDS as the denied
target: its independent security group would also deny an unauthorized source,
so a timeout could not establish which layer made the decision. The committed
probe instead uses two Deployment-owned client Pods against the same in-cluster
Service and TCP path. Neither Pod has a `SecurityGroupPolicy`; only the label
selected by the destination NetworkPolicy differs.

This disposable namespace is the one documented exception to Flux's ownership
of platform state: it is a qualification fixture, not an application or
long-lived platform object. The operator must apply the committed file exactly,
must not modify a platform namespace, and must complete the cleanup assertion in
the same session.

```bash
kubectl apply -f hack/qualification/eks-network-policy-probe.yaml
kubectl -n network-policy-probe rollout status \
  deployment/network-policy-probe-server --timeout=2m
kubectl -n network-policy-probe rollout status \
  deployment/network-policy-probe-allowed --timeout=2m
kubectl -n network-policy-probe rollout status \
  deployment/network-policy-probe-denied --timeout=2m

ALLOWED_OUTPUT="$(kubectl -n network-policy-probe exec \
  deployment/network-policy-probe-allowed -- \
  wget -q -T 5 -O - http://network-policy-probe:8080/)"
test "$ALLOWED_OUTPUT" = "network-policy-ok"

set +e
kubectl -n network-policy-probe exec \
  deployment/network-policy-probe-denied -- \
  wget -q -T 5 -O - http://network-policy-probe:8080/ >/dev/null 2>&1
DENIED_STATUS=$?
set -e
test "$DENIED_STATUS" -ne 0

kubectl delete namespace network-policy-probe --wait=true
! kubectl get namespace network-policy-probe >/dev/null 2>&1
```

Both results are required: allowed must return the exact marker and denied must
fail. Before any signer suspension changes, open and merge a reviewed,
sanitized evidence note under `docs/evidence/` that records the UTC time, tested
`main` commit, VPC CNI add-on version and four non-secret settings, three
Deployment rollout results, allowed marker, nonzero denied status, and namespace
cleanup. Do not record account IDs, ARNs, security-group IDs, Pod/Service IPs,
credentials, or raw environment dumps. A chat transcript or an uncommitted
terminal scrollback is not durable evidence.

If an AWS SecretStore is not Ready, diagnose the Pod Identity association and
IAM resource policy. Never add static AWS credentials to a Kubernetes Secret as
a workaround.

## 6. Admit the signer layers through separate reviewed commits

Do not remove both suspensions in one change; there are now three signer
suspensions, and each removal is its own reviewed commit.

After the RDS/Secrets Manager/TLS/network/backup inputs above are proven,
extend the bootstrap ConfigMap with the signer-only non-secret outputs. Do not
print them or substitute the base Pod Identity role:

```bash
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
READER_ROLES_JSON="$(terraform -chdir="$TF_ROOT" output -json external_secrets_reader_role_arns)"
ENGINE_READER_ROLE_ARN="$(jq -er '.engine' <<<"$READER_ROLES_JSON")"
DATABASE_READER_ROLE_ARN="$(jq -er '.database' <<<"$READER_ROLES_JSON")"
SIGNING_READER_ROLE_ARN="$(jq -er '.signing' <<<"$READER_ROLES_JSON")"
WEB3SIGNER_POD_SECURITY_GROUP_ID="$(terraform -chdir="$TF_ROOT" output -raw web3signer_pod_security_group_id)"
WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID="$(terraform -chdir="$TF_ROOT" output -raw web3signer_migration_pod_security_group_id)"

for role_arn in "$ENGINE_READER_ROLE_ARN" "$DATABASE_READER_ROLE_ARN" "$SIGNING_READER_ROLE_ARN"; do
  case "$role_arn" in
    "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${CLUSTER_NAME}-eso-engine-reader"|"arn:aws:iam::${AWS_ACCOUNT_ID}:role/${CLUSTER_NAME}-eso-database-reader"|"arn:aws:iam::${AWS_ACCOUNT_ID}:role/${CLUSTER_NAME}-eso-signing-reader") ;;
    *) echo "reader role ARN is outside the selected AWS account" >&2; exit 1 ;;
  esac
done

for group_id in "$WEB3SIGNER_POD_SECURITY_GROUP_ID" "$WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID"; do
  case "$group_id" in
    sg-*) ;;
    *) echo "Web3Signer Pod security-group output is not an EC2 security-group ID" >&2; exit 1 ;;
  esac
done

kubectl -n flux-system create configmap aws-secret-store-role-arns \
  --from-literal=EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN="$ENGINE_READER_ROLE_ARN" \
  --from-literal=EXTERNAL_SECRETS_DATABASE_READER_ROLE_ARN="$DATABASE_READER_ROLE_ARN" \
  --from-literal=EXTERNAL_SECRETS_SIGNING_READER_ROLE_ARN="$SIGNING_READER_ROLE_ARN" \
  --from-literal=WEB3SIGNER_POD_SECURITY_GROUP_ID="$WEB3SIGNER_POD_SECURITY_GROUP_ID" \
  --from-literal=WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID="$WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n flux-system annotate configmap aws-secret-store-role-arns \
  kustomize.toolkit.fluxcd.io/prune=disabled --overwrite
```

First open a PR changing only
`clusters/dev/signer-infrastructure-configs.yaml` to `suspend: false`. With
`StrictPostBuildSubstitutions=true`, any missing signer-only key fails this
layer before a partial adapter is applied. After merge, verify every applied
reference matches the Terraform output held in memory:

```bash
flux reconcile kustomization signer-infrastructure-configs --with-source
kubectl get clustersecretstore aws-database-secrets aws-signing-secrets
kubectl get securitygrouppolicy -n signing web3signer
kubectl get securitygrouppolicy -n database web3signer-schema

test "$(kubectl get clustersecretstore aws-database-secrets -o jsonpath='{.spec.provider.aws.role}')" = \
  "$DATABASE_READER_ROLE_ARN"
test "$(kubectl get clustersecretstore aws-signing-secrets -o jsonpath='{.spec.provider.aws.role}')" = \
  "$SIGNING_READER_ROLE_ARN"
test "$(kubectl -n signing get securitygrouppolicy web3signer \
  -o jsonpath='{.spec.securityGroups.groupIds[0]}')" = \
  "$WEB3SIGNER_POD_SECURITY_GROUP_ID"
test "$(kubectl -n database get securitygrouppolicy web3signer-schema \
  -o jsonpath='{.spec.securityGroups.groupIds[0]}')" = \
  "$WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID"
```

Only then open a second PR changing
`clusters/dev/signer-prerequisites.yaml` to `suspend: false`. After merge,
verify:

```bash
flux reconcile kustomization signer-prerequisites --with-source
kubectl -n database get externalsecret web3signer-postgres-app
kubectl -n database get job web3signer-schema-v12
kubectl -n database logs job/web3signer-schema-v12 -c flyway
```

The ExternalSecret must be Ready and the migration Job Complete. Logs must show
all expected migrations applied or validated; they must not expose credentials.
Prove that this exact Pod received a branch ENI carrying the dedicated migration
group. The EC2 response is intentionally held in memory and reduced to a boolean
so IDs and addresses do not enter the transcript:

```bash
MIGRATION_POD_IP="$(kubectl -n database get pods \
  -l app.kubernetes.io/name=web3signer-schema,app.kubernetes.io/component=database-migration \
  -o json | jq -er '.items | sort_by(.metadata.creationTimestamp) | last | .status.podIP')"
MIGRATION_ENI_JSON="$(aws ec2 describe-network-interfaces \
  --filters "Name=addresses.private-ip-address,Values=${MIGRATION_POD_IP}" \
  --output json)"
jq -e --arg expected "$WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID" '
  [.NetworkInterfaces[] |
    select(.Description | startswith("aws-k8s-branch-eni"))] as $branch_enis |
  ($branch_enis | length) == 1 and
  ($branch_enis[0].Groups | length) == 1 and
  $branch_enis[0].Groups[0].GroupId == $expected
' <<<"$MIGRATION_ENI_JSON" >/dev/null
unset MIGRATION_POD_IP MIGRATION_ENI_JSON
```

If the Job ran but this assertion fails, leave `apps` suspended. A successful
database connection is not a substitute for proving which network identity the
Pod received.

Only then open a third PR changing `clusters/dev/apps.yaml` to
`suspend: false`. The committed assignment is still stopped and non-signing, so
this admits Web3Signer with an empty key directory plus dashboards and retained
PVC declarations—not validator duties. Verify zero loaded keys before any node
activation request. Also prove the Web3Signer Pod has a branch ENI carrying only
the expected runtime group before calling this layer qualified:

```bash
kubectl -n signing rollout status deployment/web3signer --timeout=5m
WEB3SIGNER_POD_IP="$(kubectl -n signing get pods \
  -l app.kubernetes.io/name=web3signer \
  -o json | jq -er '.items | sort_by(.metadata.creationTimestamp) | last | .status.podIP')"
WEB3SIGNER_ENI_JSON="$(aws ec2 describe-network-interfaces \
  --filters "Name=addresses.private-ip-address,Values=${WEB3SIGNER_POD_IP}" \
  --output json)"
jq -e --arg expected "$WEB3SIGNER_POD_SECURITY_GROUP_ID" '
  [.NetworkInterfaces[] |
    select(.Description | startswith("aws-k8s-branch-eni"))] as $branch_enis |
  ($branch_enis | length) == 1 and
  ($branch_enis[0].Groups | length) == 1 and
  $branch_enis[0].Groups[0].GroupId == $expected
' <<<"$WEB3SIGNER_ENI_JSON" >/dev/null
unset WEB3SIGNER_POD_IP WEB3SIGNER_ENI_JSON
```

Record both branch-ENI assertions in the same reviewed, sanitized EKS evidence
series. The raw EC2 response must not be committed.

## 7. Runtime gates still required for the first pair

Flux bootstrap alone does not authorize a sync or a validator:

- the Engine JWT JSON property exists and projects through
  `aws-engine-secrets`;
- one zonal Ethereum node group is explicitly resumed in the intended PVC zone;
- the generation-addressed Ephemery adapter and artifact/checkpoint inputs are
  pinned and rendered for the selected clients;
- inbound/outbound P2P behavior and its AWS security path are reviewed;
- the non-signing lifecycle request is merged and Flux reports the pair Ready;
- EL and CL report the correct network, healthy peer counts, decreasing sync
  distance, and finalized-chain progress;
- validator duties remain disabled until the separate key, slashing-history
  restore, doppelganger, uniqueness, clock, and activation gates pass.

Use [`eks-ephemery-sync.md`](eks-ephemery-sync.md) for the exact node-only
admission, P2P, identity, sustained-sync, stop, and same-AZ resume gates.

## 8. Pause and rollback

To pause node reconciliation, first merge the assignment to `stopped`, verify
client Pods and the P2P LoadBalancer are absent, then merge `suspend: true` for
`node-apps` and scale the correct zonal Ethereum node group to zero through the
guarded capacity runbook. Suspension alone does not stop already-created
workloads. To pause the signer branch, stop any dependent duties first and then
suspend `apps`; keep the prerequisite and infrastructure layers until their
retained data/network interfaces have been inspected.

If the signer prerequisite fails, leave `apps` suspended, inspect External
Secrets/Flyway events and logs, correct the declared adapter, and reconcile
again. Do not bypass Flux with `kubectl apply` or Helm, and do not weaken TLS,
NetworkPolicy, Pod Identity, or signing defaults to make a readiness check green.
