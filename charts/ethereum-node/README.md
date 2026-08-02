# Ethereum node-pair chart

This chart is the first executable slice of the normalized node-pair contract. Version `0.2.0` intentionally supports only Geth + Lighthouse on Hoodi or Sepolia. The product catalog already models all sixteen EL/CL combinations; adding an enum here would make a combination deployable, so the other adapters remain absent until their flags, ports, probes, metrics, remote-signing behavior, and lifecycle tests exist.

## Safety properties

- The default lifecycle is `stopped`; no workload or secret projection is rendered.
- The chart never mounts a validator keystore or Web3Signer database credentials.
- Validator duties use the shared `web3signer.signing.svc.cluster.local` service.
- Enabling a validator without an explicit slashing-protection acknowledgment fails rendering.
- `stopped` retains lifecycle metadata and PVCs; `archived` removes pair PVC declarations but does not remove validator identity, signing-key custody, or shared slashing history.
- Images used by this vertical slice are pinned by human-readable tag and immutable multi-architecture digest.

This is not yet included by the local Flux application overlay. That promotion happens after the platform-smoke stack passes runtime verification and the machine has enough disk for a real chain sync.

Run the static lifecycle tests with:

```bash
make helm-template
```
