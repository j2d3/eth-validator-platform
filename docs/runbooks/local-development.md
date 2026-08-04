# Local development runbook

## Purpose

This runbook creates the local `kind` environment used to qualify the GitOps, PostgreSQL, Web3Signer, observability, and Ethereum-client contracts before any AWS resources are created.

It does **not** qualify EKS, EBS, RDS, IAM, KMS, AWS networking, multi-AZ behavior, or autoscaling. The default `kindnet` networking layer accepts NetworkPolicy objects but does not enforce them; policy schema and desired state are tested locally, while runtime enforcement requires a later CNI qualification.

## Resource profiles

Start Docker with enough assigned resources for the intended profile:

| Profile | Suggested starting allocation | Notes |
|---|---|---|
| `platform-smoke` | 8 CPU, 16 GiB RAM, 40 GiB free disk | Flux, operators, PostgreSQL, Web3Signer, Prometheus, and Grafana; no Ethereum sync |
| `real-node` | 12+ CPU, 24–32 GiB RAM, hundreds of GiB free disk | One EL/CL pair; actual requirements vary by client, network state, and retention profile |

These are starting budgets, not capacity guarantees. Dashboards and qualification notes record observed CPU, memory, storage growth, IOPS, and sync duration per client pair.

The `platform-smoke` profile includes a local-sized logging path. Alloy runs as a
DaemonSet and tails Pod logs through the Kubernetes API, filtering discovery to
the Pod's own node; it does not mount host log directories. Loki runs as one
persistent single-binary replica with a 5 GiB claim and 24-hour Compactor
retention. This is intentionally a laptop topology, not the proposed production
shape: EKS qualification must evaluate object storage or a managed log backend,
multi-AZ failure behavior, ingestion limits, stream-label cardinality, retention,
and cost.

The local P2P port mappings bind only to `127.0.0.1`. Clients can initiate outbound peer connections, but this initial configuration is not publicly reachable and therefore does not test inbound internet peering.

The `observability` namespace is a deliberate Pod Security exception. Prometheus
node-exporter needs host namespaces, host paths, and host port `9100`, so that
namespace enforces the `privileged` profile while continuing to audit and warn
against `restricted`. Application, signing, database, and secret namespaces
remain `restricted`. A production hardening step is to move host-level collectors
into a dedicated privileged namespace so Grafana, Prometheus, and Alertmanager can
return to restricted enforcement.

CloudNativePG generates an application Secret whose `host` value is a short
Service name intended for consumers in the database namespace. External Secrets
copies that credential contract into the isolated signing namespace. The local
infrastructure adapter therefore creates an `ExternalName` Service with the same
short name in `signing`, resolving it to the writer Service in `database`. The
secret remains unmodified; the later AWS adapter instead supplies the native RDS
endpoint through Secrets Manager.

## 1. Install tools

On this project workstation (macOS arm64), install pinned project-local copies of `kind`, Flux, and Terraform without changing the global Homebrew environment:

```bash
make tools
```

The installer verifies upstream SHA-256 checksums and writes only to the Git-ignored `.local/bin` directory. The repository pins `kind` 0.32.0, Flux 2.8.8, Terraform 1.15.8, and the `kindest/node` Kubernetes 1.35.5 image digest. Check the workstation:

```bash
make local-preflight
```

Verify that each digest-pinned third-party image still matches the numeric runtime
identity declared in its Kubernetes security context:

```bash
make container-contracts
```

This online check pulls the exact image digest and runs only its identity utility
with no network, mounts, Linux capabilities, or writable root filesystem. It
prevents an image upgrade from silently invalidating `runAsNonRoot` or changing
the expected UID/GID. The regular `make check` target remains offline.

## 2. Repair and select GitHub authentication

Flux must pull the private `j2d3/eth-validator-platform` repository. Authenticate the GitHub CLI as `j2d3`, then verify without printing a token:

```bash
gh auth login -h github.com
gh auth switch -h github.com -u j2d3
gh auth status -h github.com
gh auth token -h github.com -u j2d3 >/dev/null
```

Flux bootstrap can use a fine-grained token limited to this repository. It needs repository administration access to create a read-only deploy key, plus read/write Contents and read Metadata access. Exporting the CLI token avoids placing its value in shell history:

```bash
export GITHUB_TOKEN="$(gh auth token -h github.com -u j2d3)"
```

## 3. Push the implementation commit

Flux can reconcile only committed and pushed desired state. The bootstrap command intentionally refuses a dirty worktree.

```bash
git status --short
git push origin main
```

## 4. Create local Kubernetes

```bash
make local-up
```

This creates `kind-eth-validator-local` from the digest-pinned Kubernetes image and waits for the node to become Ready. It does not install applications directly.

The lab writes a dedicated kubeconfig to `.local/kubeconfig`; scripts never change the workstation's global current context. For the direct diagnostic commands later in this runbook, scope the shell explicitly:

```bash
export KUBECONFIG="$PWD/.local/kubeconfig"
```

## 5. Bootstrap Flux

```bash
make local-bootstrap
```

Flux generates its controller and private-repository sync manifests under `clusters/local/flux-system`, commits them to `main`, installs the controllers, and begins the declared reconciliation chain:

```text
infrastructure-controllers
          |
          v
infrastructure-configs
          |
          v
 signer-prerequisites
          |
          v
        apps
```

The signer-prerequisites layer runs the versioned Web3Signer PostgreSQL schema
Job in the `database` namespace. The Job copies the migrations shipped in the
digest-pinned Web3Signer image and applies them with a separately digest-pinned
Flyway image. Flux does not admit the Web3Signer application layer until this
Job completes successfully. Flyway records each applied migration in
`flyway_schema_history`, so deleting and reconciling the Job is a safe no-op
after all 12 migrations succeed. The Job carries Flux's force annotation because
Kubernetes Job pod templates are immutable; a reviewed template correction can
therefore replace and rerun the Job instead of wedging reconciliation. The Job
has no automatic TTL, which prevents Flux from recreating it continuously.

After bootstrap, pull the Flux-generated commit into the workstation before making another change:

```bash
git pull --ff-only origin main
```

## 6. Seed non-Git local secrets

```bash
make local-seed
```

The command:

- creates a 32-byte Engine API JWT in `secrets/local/engine-jwt.hex` if one does not exist;
- creates or updates only the `platform-secrets/engine-jwt` source Secret;
- optionally seeds `platform-secrets/validator-keystore` when both `secrets/local/validator/keystore.json` and `password.txt` already exist;
- never enables signing.

Do not copy a funded validator keystore into the optional directory until the slashing-history backup/restore and activation runbooks have been qualified.

## 7. Verify reconciliation

```bash
make local-status
kubectl -n database get job web3signer-schema-v12
kubectl -n database logs job/web3signer-schema-v12 -c copy-web3signer-migrations
kubectl -n database logs job/web3signer-schema-v12 -c flyway
kubectl -n database exec web3signer-postgres-1 -- \
  psql -U postgres -d web3signer -c \
  'SELECT installed_rank, version, description, success FROM flyway_schema_history ORDER BY installed_rank;'
kubectl -n signing rollout status deploy/web3signer --timeout=5m
kubectl -n signing get deploy,pod,svc,externalsecret
kubectl -n database get cluster,pod,pvc
kubectl -n observability get helmrelease loki alloy
kubectl -n observability get statefulset,daemonset,pod,pvc,servicemonitor
```

The migration Job uses `restartPolicy: Never`, so a failed attempt remains as a
separate Pod with inspectable init-container and Flyway logs. Diagnose the copy
step before the database step: Flyway cannot start until the pinned Web3Signer
migrations have been copied successfully.

Open Grafana through a local-only port-forward:

```bash
kubectl -n observability port-forward svc/monitoring-grafana 3000:80
```

Retrieve its generated admin password without writing it to disk:

```bash
kubectl -n observability get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 --decode
```

The provisioned dashboards are **Ethereum Platform / Local Smoke**, **Ethereum
Platform / Logs**, **Ethereum Platform / Fleet**, **Ethereum Platform /
Validator detail**, **Ethereum Platform / Signer and slashing protection**, and
**Ethereum Validator / Geth + Lighthouse**. The logging dashboard moves from
namespace to Pod and container and includes a dedicated Web3Signer stream. The
pair dashboard supports both one-validator selection and an `All` fleet view;
it remains empty while the safe `stopped` profile is selected.

### Reading the signer dashboard

**Keys loaded must be zero.** The platform is fail-closed: Web3Signer runs with
an empty key store and cannot sign. A non-zero value on this lab is a finding,
not progress.

**The two slashing counters are safety signals, not throughput.**
`eth2_slashingprotection_permitted_signings_total` counts checks that reported
safe-to-sign; `eth2_slashingprotection_prevented_signings_total` counts signings
refused because they would have violated a slashing condition. Any increase in
the *prevented* counter deserves investigation, even though it represents the
protection working as designed.

**Web3Signer cannot tell you about its database connection.** Running the pinned
`consensys/web3signer:26.4.2` image and scraping its `/metrics` endpoint with
`--slashing-protection-enabled` both false and true — the second against a
PostgreSQL with the twelve shipped migrations applied — produces the *same 48
metric families*. There is no connection-pool, JDBC, or datasource metric; the
only `pool` families are `jvm_buffer_pool_*`, `jvm_memory_pool_*` and
`http_vertx_worker_pool_*`. Signer-to-database health is therefore observed
through three other paths, and the dashboard says so rather than implying a
signal it does not have:

- process liveness (`validator_platform_signer_up`),
- the CloudNativePG panels on the same dashboard, which observe the database
  itself,
- the Web3Signer log stream on the logging dashboard.

To reproduce that finding without a cluster:

```bash
docker run --rm -p 127.0.0.1:19001:9001 \
  consensys/web3signer:26.4.2 \
  --metrics-enabled=true --metrics-host=0.0.0.0 --metrics-port=9001 \
  eth2 --network=hoodi --slashing-protection-enabled=false
curl -s http://127.0.0.1:19001/metrics | grep '^# TYPE' | wc -l
```

Database metric names come from the pinned `cloudnative-pg` chart's
`cnpg-default-monitoring` query set. The `cnpg_collector_*` series are
deliberately unused: they originate in the operator binary rather than a pinned
chart artifact, so they could not be verified offline.

Verify ingestion without exposing Loki outside the workstation:

```bash
kubectl -n observability port-forward svc/loki 3100:3100
```

In a second shell, query the last ten minutes of the signing namespace:

```bash
curl --get --silent --show-error \
  --data-urlencode 'query={cluster="kind-eth-validator-local",namespace="signing"}' \
  --data-urlencode "start=$(($(date +%s) - 600))000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  --data-urlencode 'limit=20' \
  http://127.0.0.1:3100/loki/api/v1/query_range | jq .
```

The response must have `status: "success"` and include `namespace`, `pod`,
`container`, `node`, `cluster`, `environment`, and `platform` labels. Validator
pair streams additionally receive controlled customer, assignment, network, and
client labels from Pod metadata. Do not emit secret values in application logs;
central collection does not make unsafe logging acceptable.

## 8. Request a non-signing node-pair lifecycle change

The repository contains stopped generated `HelmRelease` objects for the Hoodi
`assignment-synthetic-01` and Ephemery
`assignment-ephemery-162-synthetic` node pairs. Their source of truth is the
assignment, identity, customer, service-profile, and network-profile catalog
under `applications/`; direct edits to
`platform/apps/local/assignments/` fail `make check`.

While the assignment is stopped, its execution and consensus PVCs remain
`Pending` under the local `WaitForFirstConsumer` StorageClass because no client
Pod exists to bind them. That is the expected safe state. The generated release
disables Helm resource waiting for stopped installs and upgrades so unrelated
Git revisions still reconcile; active installs and upgrades retain normal
Helm waiting. Do not create a dummy consumer or apply the chart manually to
make a stopped PVC bind.

Before using the form for the first time, an owner must enable:

**Repository Settings → Actions → General → Workflow permissions → Allow
GitHub Actions to create and approve pull requests.**

The current repository setting is disabled by default on a new personal
repository. Enabling it permits the specifically reviewed workflow to create a
branch and PR; branch protection, CI, CODEOWNERS, and the merge wrapper still
prevent it from merging its own change. GitHub may place CI triggered by a PR
created with `GITHUB_TOKEN` in an approval-required state. A write collaborator
must approve that workflow run, or a later production adapter must use a
short-lived GitHub App installation token. Do not solve this with a long-lived
AWS or GitHub credential in the workflow.

From the GitHub Actions tab, run **Request non-signing node-pair lifecycle**
with:

- assignment: `assignment-synthetic-01` (Hoodi) or
  `assignment-ephemery-162-synthetic` (the pinned Ephemery generation);
- action: `activate` or `stop`;
- reason: an auditable explanation between 3 and 256 characters.

The workflow updates the selected assignment and regenerates its Flux HelmRelease, runs
the catalog/tests, and opens a PR. It has `contents: write` and
`pull-requests: write` only—no AWS credential, kubeconfig, OIDC token, or
cluster access. After CI and review, merge through `hack/merge-pr.sh`; Flux is
the only actor that changes Kubernetes.

For an activation request, verify the declared boundary before review:

```bash
python3 tools/render_local_assignments.py --check
git diff origin/main -- \
  applications/validators/assignments/<assignment>.yaml \
  platform/apps/local/assignments/<assignment>.yaml
```

`lifecycleState: active` must be paired with `validator.enabled: false`. This
starts Geth and the Lighthouse beacon node for sync practice; it does **not**
start the Lighthouse validator client and cannot sign. The initial workflow
refuses assignments whose catalog state already enables signing.

For Ephemery, activation first downloads and verifies the immutable generation
bundle, initializes a generation-specific execution PVC, and mounts the same
verified config/genesis directory into Lighthouse. A new Ephemery generation
requires a new profile and new PVC names; never edit the old profile in place.

After Flux reconciles an approved activation, observe rather than infer:

```bash
flux get helmreleases -n ethereum
kubectl -n ethereum get helmrelease,statefulset,pod,pvc,externalsecret
kubectl -n ethereum logs statefulset/pair-validator-synthetic-01 -c execution --tail=100
kubectl -n ethereum logs statefulset/pair-validator-synthetic-01 -c consensus --tail=100
# Ephemery uses statefulset/pair-ephemery-162-synthetic.
```

A successful first exercise is stopped → active → stopped through two reviewed
PRs. On stop, Flux removes running compute and leaves the chain-data PVC
declarations and identity record. Runtime sync/finality gates, Web3Signer key
admission, validator duties, archive deletion, and funded identities are not
authorized by this workflow.

## 9. Delete safely

```bash
make local-down
```

The wrapper refuses deletion if any lifecycle record reports `platform.galaxy-lab/signing-enabled=true`. Before deleting a cluster that has ever used a funded key, stop signer admission and complete the PostgreSQL slashing-history export/restore runbook. Local-path PVCs must be treated as disposable until that recovery procedure is implemented and evidenced.

## Troubleshooting

```bash
flux get all --all-namespaces
flux logs --all-namespaces --level=error
kubectl get events --all-namespaces --sort-by=.lastTimestamp
kubectl -n signing logs deploy/web3signer
kubectl -n database logs web3signer-postgres-1
kubectl -n observability logs statefulset/loki
kubectl -n observability logs daemonset/alloy
```

If Flux cannot authenticate, confirm that the token belongs to `j2d3` and has repository-administration permission for deploy-key creation. If a chart source fails, inspect its `HelmRepository` and `HelmRelease` status before retrying; do not bypass Flux with a manual Helm installation.
