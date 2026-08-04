# Ethereum node-pair chart

This chart implements Geth + Lighthouse and Reth + Lighthouse with two reviewed testnet adapters: the clients' built-in Hoodi configuration and the digest-pinned `ephemery-162` custom-network bundle. The schema reserves the built-in Sepolia selector for a later reviewed profile. Mainnet is not deployable through this lab chart. Other client adapters remain absent until their flags, ports, probes, metrics, remote-signing behavior, and lifecycle tests exist.

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
- Artifact-mode validator duties require an explicit qualified signer binding and a generation-pinned consensus configuration ConfigMap. The validator client uses Web3Signer and never mounts signing keys.
- Node and validator Pods run as explicit UID/GID 1000 with a restricted seccomp/capability/root-filesystem posture. Bounded per-container `/tmp` volumes preserve that read-only root contract for clients that need runtime scratch space.
- Persistent-network defaults retain a node PDB and 300-second node shutdown budget. The one-replica EKS Ephemery Spot profile omits that non-redundant node PDB and uses a 30-second node shutdown bound. A signing validator client has its own `minAvailable: 1` PDB.
- TCP startup/readiness/liveness probes prove that the client API processes answer; they do not claim peer health, sync progress, correct chain identity, or authorization to sign.
- P2P has its own configurable Service. The default is ClusterIP with no fixed NodePort; the EKS Ephemery profile selects one internet-facing NLB carrying only 30303/TCP+UDP, 9000/TCP+UDP, and 9001/UDP. JSON-RPC, Engine API, beacon API, and metrics remain internal.

The generated catalog releases feed both local and EKS overlays. The local overlay explicitly disables validator duties for the signed Ephemery assignment. The EKS overlay admits that assignment only after the shared signer application reports Ready. The non-signing lifecycle workflow refuses signed assignments.

The EKS dev cluster has run both Ephemery pairs through Flux. Geth/Lighthouse and Reth/Lighthouse reached the current network head during their qualification runs. The signing render is separately guarded by the registered identity, qualified Web3Signer network binding, slashing-protection acknowledgment, doppelganger acknowledgment, and signer-layer Flux dependency.

## Storage

Every claim names its StorageClass explicitly; `values.schema.json` rejects an empty `storageClassName` because Kubernetes reads it as "ignore every StorageClass". `values.yaml` defaults to the local `standard` class and 20/10/5 GiB claims. `values-eks-hoodi-storage.yaml` declares 200/50/5 GiB. `values-eks-ephemery.yaml` declares 50/20/5 GiB and renders the 5 GiB validator claim only while duties are enabled. Both EKS profiles select the encrypted `gp3` class from `platform/infrastructure/configs/dev`. `gp3` volumes may expand but cannot shrink, so generation-specific claims remain part of lifecycle identity. Neither environment inherits the other's class.

Run the static lifecycle tests with:

```bash
make helm-template
```
