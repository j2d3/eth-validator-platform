# Ethereum node-pair chart

This chart is the first executable slice of the normalized node-pair contract. Version `0.2.0` intentionally supports only Geth + Lighthouse on Hoodi or Sepolia. The product catalog already models all sixteen EL/CL combinations; adding an enum here would make a combination deployable, so the other adapters remain absent until their flags, ports, probes, metrics, remote-signing behavior, and lifecycle tests exist.

## Safety properties

- The default lifecycle is `stopped`; no workload or secret projection is rendered.
- The chart never mounts a validator keystore or Web3Signer database credentials.
- Validator duties use the shared `web3signer.signing.svc.cluster.local` service.
- Enabling a validator without an explicit slashing-protection acknowledgment fails rendering.
- `stopped` retains lifecycle metadata and PVCs; `archived` removes pair PVC declarations but does not remove validator identity, signing-key custody, or shared slashing history.
- The lifecycle record emits `platform.galaxy-lab/signing-enabled`, which binds any future signing profile to the guarded local-cluster teardown path.
- Images used by this vertical slice are pinned by human-readable tag and immutable multi-architecture digest.

The local Flux application overlay now includes one catalog-generated HelmRelease in the safe `stopped` state. The stopped first install disables Helm's readiness wait because a WaitForFirstConsumer StorageClass has no Pod to bind its retained PVCs; a stopped → active upgrade uses the normal waiting upgrade action. The GitHub lifecycle form can request active or stopped desired state, but its active projection keeps `validator.enabled=false`: it starts only Geth and the Lighthouse beacon node for sync qualification.

That is declared implementation, not runtime evidence. The pair has not yet completed a Flux-managed lifecycle or chain sync on this workstation.

## Storage

Every claim names its StorageClass explicitly; `values.schema.json` rejects an empty `storageClassName` because Kubernetes reads it as "ignore every StorageClass". `values.yaml` defaults to the local `standard` class and the laptop-sized 20/10/5 GiB claims. `values-eks-hoodi-storage.yaml` is the EKS override — the encrypted `gp3` class from `platform/infrastructure/configs/dev` and a proposed 200/50/5 GiB Hoodi profile whose sizes are a first-sync hypothesis rather than a measurement. Neither environment inherits the other's class: `standard` does not exist on EKS, and the EBS class does not exist in `kind`.

Run the static lifecycle tests with:

```bash
make helm-template
```
