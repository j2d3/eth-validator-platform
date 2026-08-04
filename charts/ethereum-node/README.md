# Ethereum node-pair chart

This chart is the first executable slice of the normalized node-pair contract. Version `0.3.0` intentionally supports only Geth + Lighthouse with two reviewed testnet adapters: the clients' built-in Hoodi configuration and the digest-pinned `ephemery-162` custom-network bundle. The schema reserves the built-in Sepolia selector for a later reviewed profile. Mainnet is deliberately not deployable through this lab chart. The product catalog already models all sixteen EL/CL combinations; adding an enum here would make a combination deployable, so the other adapters remain absent until their flags, ports, probes, metrics, remote-signing behavior, and lifecycle tests exist.

## Safety properties

- The default lifecycle is `stopped`; no workload or secret projection is rendered.
- The chart never mounts a validator keystore or Web3Signer database credentials.
- Validator duties use the shared `web3signer.signing.svc.cluster.local` service.
- Enabling a validator without an explicit slashing-protection acknowledgment fails rendering.
- `stopped` retains lifecycle metadata and PVCs; `archived` removes pair PVC declarations but does not remove validator identity, signing-key custody, or shared slashing history.
- The lifecycle record emits `platform.galaxy-lab/signing-enabled`, which binds any future signing profile to the guarded local-cluster teardown path.
- Images used by this vertical slice are pinned by human-readable tag and immutable multi-architecture digest.
- Network selection comes from a reviewed `NetworkProfile`; the chart receives immutable chain identity and typed client selectors, not a friendly string or operator-supplied argument list.
- Pods and retained PVCs are annotated with the full network-identity fingerprint. Resetting networks also include a deterministic hash of the full node-pair reference and a fingerprint prefix in every PVC name, so long pair names cannot collide and a later generation cannot silently mount the old generation's databases.
- The Ephemery loader downloads only the immutable generation URL, limits transfer size/time, verifies the full bundle SHA-256, and extracts an allowlisted member set before either client starts.
- Geth custom-genesis initialization refuses an unmarked non-empty volume and rejects a marker for any other network identity. Lighthouse reads the verified generation directory through `--testnet-dir`.
- Artifact-mode profiles are node-only in this slice: chart schema and templates reject `validator.enabled=true`, and the shared Web3Signer deployment remains bound to Hoodi.
- Node and validator Pods run as explicit UID/GID 1000 with a restricted seccomp/capability/root-filesystem posture. Bounded per-container `/tmp` volumes preserve that read-only root contract for clients that need runtime scratch space.
- Persistent-network defaults retain a node PDB and 300-second node shutdown budget. The one-replica, non-signing EKS Ephemery Spot profile deliberately omits that non-redundant PDB and follows Amazon EKS guidance with a 30-second shutdown bound; only the interruption exercise in the runbook can qualify recovery.
- TCP startup/readiness/liveness probes prove that the client API processes answer; they do not claim peer health, sync progress, correct chain identity, or authorization to sign.
- P2P has its own configurable Service. The default is ClusterIP with no fixed NodePort; the EKS Ephemery profile selects one internet-facing NLB carrying only 30303/TCP+UDP, 9000/TCP+UDP, and 9001/UDP. JSON-RPC, Engine API, beacon API, and metrics remain internal.

The local Flux application overlay includes Hoodi and Ephemery catalog-generated HelmReleases in the safe `stopped` state. A stopped first install disables Helm's readiness wait because a WaitForFirstConsumer StorageClass has no Pod to bind its retained PVCs; a stopped → active upgrade uses the normal waiting upgrade action. The GitHub lifecycle form can request active or stopped desired state for either assignment, but its active projection keeps `validator.enabled=false`: it starts only Geth and the Lighthouse beacon node for sync qualification.

That is declared implementation, not EKS runtime evidence. Container contracts exercise the immutable Ephemery bundle, both non-root client images, exact Geth sync metrics, and exact Lighthouse head/clock/peer metrics in isolated containers. Two exact Lighthouse processes establish one local peer relationship so the peer-count contract proves a non-zero sample without contacting a public Ethereum network. Neither network has completed a Flux-managed lifecycle or chain sync yet.

## Storage

Every claim names its StorageClass explicitly; `values.schema.json` rejects an empty `storageClassName` because Kubernetes reads it as "ignore every StorageClass". `values.yaml` defaults to the local `standard` class and the laptop-sized 20/10/5 GiB claims. `values-eks-hoodi-storage.yaml` is the permanent-testnet EKS hypothesis at 200/50/5 GiB. `values-eks-ephemery.yaml` is the reset-aware node-only profile at 50/20/5 GiB, with the validator claim absent while duties are disabled. Both EKS profiles select the encrypted `gp3` class from `platform/infrastructure/configs/dev`; neither is runtime sizing evidence. `gp3` volumes may expand but cannot shrink, so generation-specific claims remain part of lifecycle identity. Neither environment inherits the other's class: `standard` does not exist on EKS, and the EBS class does not exist in `kind`.

Run the static lifecycle tests with:

```bash
make helm-template
```
