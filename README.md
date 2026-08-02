# Ethereum Validator Platform Lab

A spec-built, GitOps-operated Ethereum validator platform for learning and demonstrating institutional staking-platform practices. The platform is designed to run completely on local Kubernetes before its AWS adapters are provisioned on EKS.

The approved product and architecture contract is [docs/prd/001-dynamic-validator-platform.md](docs/prd/001-dynamic-validator-platform.md).

## Current implementation status

| Capability | Declared implementation | Runtime evidence |
|---|---|---|
| Product and architecture specification | Approved repository baseline | Specification and validation contracts committed |
| Local `kind` cluster | Digest-pinned local cluster contract | Cluster creation and teardown guard verified |
| Flux reconciliation | Controllers → infrastructure configs → signer prerequisites → apps | Controllers, configs, and apps verified; new signer prerequisite is API-server validated and awaits merge-time reconciliation |
| Local PostgreSQL and shared Web3Signer | CloudNativePG, explicit versioned schema migration, and shared signer with an empty key store | Database readiness and signer-to-database connectivity verified; schema migration and signer readiness are the next runtime gate |
| Prometheus and Grafana | Initial stack and smoke dashboards | Prometheus, Grafana, Alertmanager, and node exporter verified Ready |
| Real Geth/Lighthouse pair | Safe stopped and non-signing chart profiles | Render contracts verified; client sync qualification has not started |
| EKS/RDS/Secrets Manager | Architecture designed | No AWS resources have been created |

Nothing in the repository authorizes validator signing by default. The local profile is `platform-smoke`, uses Hoodi configuration, and leaves validator clients stopped.

## Local architecture

```text
private GitHub repository
          |
          v
        Flux
          |
          +--> External Secrets --> restricted local source Secrets
          +--> CloudNativePG -----> local Web3Signer slashing database
          +--> Web3Signer --------> private signing API; no keys loaded by default
          +--> Prometheus/Grafana -> platform and later validator dashboards
          +--> Ethereum pair -----> stopped in platform-smoke profile
```

Local infrastructure adapters are deliberately not described as AWS emulators. `kind`, local-path volumes, CloudNativePG, and the External Secrets Kubernetes provider prove the Kubernetes and application contracts. EBS, RDS, IAM/KMS, NLB behavior, Availability Zones, and Karpenter require the later EKS qualification.

## Start locally

Read [the local development runbook](docs/runbooks/local-development.md) before creating the cluster. The short path is:

```bash
make tools
make local-preflight
make local-up
make local-bootstrap
make local-seed
make local-status
```

Flux bootstrap requires the current commit to be pushed to the private GitHub repository and a valid `j2d3` GitHub token. Local secret material is generated or read only from `secrets/local/`, which is excluded from Git.

## Repository map

| Path | Purpose |
|---|---|
| `docs/prd` | Approved product and architecture specification |
| `docs/adrs` | Durable decisions and their tradeoffs |
| `docs/runbooks` | Operator procedures and safety checks |
| `applications` | Schema-validated customer, profile, identity, and assignment catalog |
| `schemas` | Desired-state JSON Schema contracts |
| `tools` | Relational catalog validation |
| `hack` | Pinned local-tool, cluster, and secret-seeding commands |
| `clusters/local` | Flux reconciliation entry point for local Kubernetes |
| `platform/infrastructure/controllers` | Flux-managed platform operators |
| `platform/infrastructure/configs/local` | Local StorageClass, secret, and database adapters |
| `platform/apps/base` | Environment-independent application manifests |
| `platform/apps/local` | Local profile and dashboard composition |
| `charts/ethereum-node` | First Geth/Lighthouse vertical slice under runtime qualification |
| `terraform` | AWS bootstrap and later EKS environment roots |

## Safety boundary

No mnemonic, withdrawal credential, unencrypted validator key, keystore password, AWS credential, or plaintext secret belongs in Git, Terraform state, container images, workflow logs, or ordinary application manifests. A synced node is not automatically a validator, and a running Web3Signer with an empty key directory cannot sign.
