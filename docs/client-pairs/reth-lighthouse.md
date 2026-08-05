# Reth + Lighthouse

**Changes only the execution client.** Every other variable (Lighthouse beacon
+ VC, Web3Signer wiring, PVC layout, EKS overlay) is held fixed against the
Geth+Lighthouse baseline, so any behavioral difference is attributable to Reth.

## Why this pair

Reth (`ghcr.io/paradigmxyz/reth`) is a Rust EL with a pipelined-staged sync
architecture — different failure modes from Geth's snap, different metric
namespace, different on-disk layout (state under `/data/db/` instead of
`/data/geth/`). Adding it as the second EL proved that:

1. The chart's EL-adapter dispatcher extends cleanly to a non-Geth client.
2. Lighthouse's beacon + VC path is reusable across EL choices with no
   changes to the CL side.
3. The remote-signer contract with Web3Signer is EL-agnostic.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-reth-lighthouse.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-reth.yaml`
- **Registered ValidatorIdentity** (signing): `validator-ephemery-162-02`
- **Node pair name**: `pair-ephemery-162-synthetic-reth`

## Topology

Same shape as Geth+Lighthouse:

- `execution`: Reth (`ghcr.io/paradigmxyz/reth:v1.6.0`) — pipelined staged
  sync; no `--syncmode` flag.
- `consensus`: Lighthouse beacon node (same image as validator #1's pair).
- `validator`: Lighthouse validator client (Deployment), separate encrypted
  PVC, its own EIP-2335 keystore projected from AWS Secrets Manager.

## Network configuration

Same `ephemery-162` profile and same pinned artifact bundle as Geth+Lighthouse.
The chart's `networkProfile.clients.reth` slot declares the Reth adapter for
this network in artifact-bundle mode; Reth accepts the same digest-verified
`genesis.json` Geth does.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3` — separate from validator #1's PVC
  because it belongs to a different node pair (different `nodePairRef`).
- Consensus PVC: 20 GiB encrypted `gp3`.
- Validator PVC: 5 GiB encrypted `gp3`.

The `reth-consensus` PVC was reset during the sync qualification cycle when a
retained Lighthouse database refused checkpoint sync ("database already
exists"); a stop → destroy CL PVC only → reactivate cycle recovered without
touching the execution PVC or slashing state.

## Engine API and remote-signer wiring

Identical to Geth+Lighthouse — the beacon and VC don't know which EL is
serving the Engine API.

## Client-specific command-line adaptations

- No `--syncmode` (Reth pipeline is always full-derivation).
- `reth node --chain <genesis> --datadir /data --bootnodes <enodes> --http
  --http.addr 0.0.0.0 --http.port 8545 --http.api eth,net,web3
  --authrpc.addr 0.0.0.0 --authrpc.port 8551 --authrpc.jwtsecret /jwt/jwt.hex
  --metrics 0.0.0.0:6060 --log.file.max-files 0`
- `--log.file.max-files 0` disables Reth's rolling file logger, which
  otherwise tries to create `$HOME/.cache/reth/logs` on the read-only root
  filesystem (fixed in #104).

## Metric normalization

Reth's metric namespace is distinct from Geth's:

- `reth_blockchain_tree_canonical_chain_height` → normalized `headBlock`.
- `reth_network_connected_peers` → normalized `peers`.
- **`headHeader` deliberately omitted**: Reth's staged pipeline advances
  header and block together; mapping both concepts to the same series would
  publish a permanent `internal_sync_distance = 0` regardless of actual
  progress — worse than empty telemetry (PR #96, per PRD §12.5). The
  recording rules therefore skip Reth from the `head_header` and
  `internal_sync_distance` unions.

## Non-signing qualification

Ran on EKS as `signingEnabled: false` for extended live sync with advancing
heads and non-zero peers on both EL and CL sides before validator #2's
identity was generated and bound.

## Signing qualification

- Deposit: 32 tETH, validator index `30202`, activation epoch `1166`.
- Web3Signer key #2 loaded alongside key #1 (proving the shared signer tier
  supports multiple disjoint identities).
- First attributable attestation: slot `37315`.
- Doppelganger detection completed with no duplicate.
- Existing signing pair (validator #1 on Geth+Lighthouse) kept signing
  through Web3Signer's rollout with zero prevented checks.

## Problems encountered and corrections

- **Read-only-root vs Reth file logger** (#104): described above.
- **Reth-vs-Geth metric semantics** (#96): initial adapter mapped both
  `headBlock` and `headHeader` to the same Reth series, publishing a
  confident-looking `sync_distance = 0`. Corrected to omit `headHeader` and
  guard the recording rule with `hasKey $spec.metrics "headHeader"`.
- **EKS-overlay patches by name** (#122's lesson): every added Ephemery
  release must receive the shared `valuesFiles` + telemetry + Engine JWT
  patches in `platform/apps/nodes/dev/kustomization.yaml`, otherwise the
  pair silently falls back to local kind defaults on EKS.

## Remaining unqualified

- Same list as validator #1 plus: proposal duty (epoch-scheduled), long-term
  attestation effectiveness, and any lifecycle transition of the running
  identity.

## References

- Chart adapter PR (Reth EL): [#93](https://github.com/j2d3/eth-validator-platform/pull/93)
- Catalog / activation PR: [#102](https://github.com/j2d3/eth-validator-platform/pull/102) (non-signing) + [#129](https://github.com/j2d3/eth-validator-platform/pull/129) (signing flip)
- Metric-name fix: [#96](https://github.com/j2d3/eth-validator-platform/pull/96)
- Reth file-logger fix: [#104](https://github.com/j2d3/eth-validator-platform/pull/104)
