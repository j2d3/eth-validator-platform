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

Loki and Alloy are part of the approved architecture but are not in the current platform-smoke manifests. They follow after the first metrics stack passes runtime verification, so the repository does not yet claim centralized logging as implemented.

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
        apps
```

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
kubectl -n signing get deploy,pod,svc,externalsecret
kubectl -n database get cluster,pod,pvc
```

Open Grafana through a local-only port-forward:

```bash
kubectl -n observability port-forward svc/monitoring-grafana 3000:80
```

Retrieve its generated admin password without writing it to disk:

```bash
kubectl -n observability get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 --decode
```

The initial dashboards are **Ethereum Platform / Local Smoke** and **Ethereum Validator / Geth + Lighthouse**. The pair dashboard supports both one-validator selection and an `All` fleet view; it remains empty while the safe `stopped` profile is selected.

## 8. Delete safely

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
```

If Flux cannot authenticate, confirm that the token belongs to `j2d3` and has repository-administration permission for deploy-key creation. If a chart source fails, inspect its `HelmRepository` and `HelmRelease` status before retrying; do not bypass Flux with a manual Helm installation.
