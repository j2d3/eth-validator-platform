# ADR 0001: Qualify the application platform locally before AWS

- Status: Accepted
- Date: 2026-08-01
- Decision owners: Platform engineering
- Related specification: [Dynamic Validator Platform PRD](../prd/001-dynamic-validator-platform.md)

## Context

The platform ultimately targets Amazon EKS, but its first learning goals are Flux reconciliation, Web3Signer remote signing, lifecycle safety, client-pair adapters, and observability. Paying for AWS while those application contracts are still changing would slow iteration and mix application failures with EKS, IAM, networking, and storage failures.

The local environment must therefore exercise the real Kubernetes resources and service protocols. It must also be honest about which AWS properties it cannot prove.

## Decision

Build and qualify the complete application path on a pinned single-node `kind` cluster before provisioning AWS:

- Flux bootstraps from the same private GitHub repository and owns ongoing reconciliation.
- External Secrets Operator uses its Kubernetes provider against a restricted, operator-seeded source namespace.
- CloudNativePG provides the local PostgreSQL endpoint and credential contract used by shared Web3Signer.
- kube-prometheus-stack provides Prometheus, Alertmanager, and Grafana.
- The real Ethereum execution, consensus, and validator-client containers run in the `real-node` profile; the default `platform-smoke` profile starts no Ethereum compute and cannot sign.
- Local signing remains disabled unless explicit identity, uniqueness, sync, slashing-protection, doppelganger, and recovery gates pass.

AWS later replaces only the environment adapters: `kind` with EKS, local-path storage with EBS, the Kubernetes secret provider with Secrets Manager plus workload identity, and CloudNativePG with RDS PostgreSQL. Application-facing names and contracts remain stable.

## Alternatives considered

### Docker Compose

Compose would be light and convenient for client processes, but it would bypass Flux, Kubernetes scheduling and security contexts, External Secrets resources, Helm/Kustomize composition, and Prometheus Operator resources. It would create a second deployment model.

### Minikube

Minikube is capable, but `kind` is smaller for a disposable CI-like cluster and aligns directly with Flux's local getting-started path.

### k3d

k3d is also a strong local option. Its k3s distribution changes more of the control-plane and bundled-component surface than this lab needs; upstream Kubernetes-in-containers is preferable here.

### Start directly on EKS

This would prove AWS behavior sooner, but every edit would carry provisioning time and cost. It is retained as the mandatory parity and qualification phase, not the development starting point.

## Consequences

Benefits:

- fast, disposable reconciliation loops without AWS credentials or charges;
- the same GitOps and application manifests are exercised before cloud provisioning;
- failure domains are easier to isolate while learning Flux and Web3Signer;
- a safe non-signing profile can be tested before large chain-data downloads.

Limitations:

- default `kindnet` accepts NetworkPolicy objects but does not enforce them;
- a single local node cannot demonstrate Availability Zone failure, multi-node disruption, or autoscaling;
- local-path volumes and CloudNativePG do not qualify EBS or RDS durability and recovery;
- local Secrets do not qualify IAM, Pod Identity, KMS, or Secrets Manager policy;
- loopback P2P mappings do not qualify internet-facing load balancers or real inbound peering;
- real EL/CL sync still needs substantial CPU, memory, bandwidth, and hundreds of GiB of disk.

These limitations are explicit test gaps. They are not treated as passed because the local stack works.
