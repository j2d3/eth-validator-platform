# Geth + Teku

**Changes only the consensus client.** The EL side is held to the proven
Geth baseline; every observed difference is attributable to the Teku beacon
node and Teku validator client.

## Why this pair

Teku (`consensys/teku`) is Consensys's Java beacon node and validator
client — a fundamentally different runtime (JVM), a different metric
namespace, and a different remote-signing API contract than Lighthouse.
Adding Teku established:

1. The chart's CL-adapter dispatcher extends cleanly to a non-Lighthouse
   client.
2. The validator-client template can dispatch between Lighthouse VC and
   Teku VC without duplicating the outer Deployment structure.
3. Web3Signer's remote-signer contract works with an EL-agnostic CL — no
   Lighthouse-specific assumptions leaked into the signer layer.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-geth-teku.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-teku.yaml`
- **Registered ValidatorIdentity** (signing): `validator-ephemery-162-03`
- **Node pair name**: `pair-ephemery-162-synthetic-teku`

## Topology

- `execution`: Geth (same image as validator #1's pair).
- `consensus`: Teku beacon node (`consensys/teku:26.7.1`) — Java, runs from
  `/opt/teku/bin/teku`.
- `validator`: Teku validator client (same image), separate Deployment,
  separate encrypted PVC, projects the Web3Signer-issued key from AWS
  Secrets Manager.

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle. Teku's beacon accepts
the consensus `config.yaml` via `--network=<file>` (Lighthouse uses
`--testnet-dir=<directory>`), so the chart's Teku helper points at the exact
file inside the mounted artifact bundle. See #115 for why the consensus
config is mounted from a local ConfigMap rather than fetched at startup:
Teku's predefined Ephemery preset tried to fetch bootnodes over HTTPS at
startup, which the signer's DNS+RDS-only NetworkPolicy correctly denied.

## Client-specific command-line adaptations

Beacon:

- `--network=/network/files/config.yaml` (file, not directory).
- `--checkpoint-sync-url=<primary>` (Codex caught the initial adapter's
  incorrect `--initial-state=` — that flag takes a direct SSZ file URL, not
  a Beacon API base URL).
- `--p2p-discovery-bootnodes=<comma-separated>`.
- `--ee-endpoint=http://127.0.0.1:8551`,
  `--ee-jwt-secret-file=/jwt/jwt.hex`.
- `--metrics-host-allowlist=*` (Teku defaults to localhost-only, which
  breaks Prometheus scraping by Pod IP — same lesson as Codex's #111
  Web3Signer fix).

Validator client:

- `validator-client` subcommand (Teku's VC is a `teku validator-client`
  invocation, not a separate binary).
- `--network=/validator-network/config.yaml`.
- `--validators-external-signer-url=<web3signer>`.
- `--validators-external-signer-public-keys=<pubkey>` — explicit key, not
  enumeration of Web3Signer's list.
- `--validators-external-signer-slashing-protection-enabled=false` —
  delegates to Web3Signer + RDS, exactly parallel to Lighthouse VC's
  `--disable-slashing-protection-web3signer`.
- `--doppelganger-detection-enabled=true`.
- `--validators-graffiti-client-append-format=DISABLED` (Teku otherwise
  appends client name/version to the operator-set graffiti).

## Metric normalization

Teku exposes its own metric namespace:

- `beacon_head_slot`, `beacon_slot`, `beacon_epoch`, `beacon_finalized_epoch`.
- `libp2p_peers` (peer count — Codex caught the initial adapter's
  `beacon_peer_count`, which is Prysm's shape, not Teku's).
- `validator_local_validator_count` (from the separate Teku VC — added when
  the VC adapter landed in #132; runtime-verified against the pinned digest
  running the hardened Pod).

## Non-signing qualification

Ran as `signingEnabled: false` on EKS with the offline Ephemery config mount
and RTS ingress. Non-signing period exercised the beacon-only path and
proved the chart-side plumbing was correct before any VC came in.

## Signing qualification

- Deposit: 32 tETH, validator index `30203`, activation epoch pending at
  time of activation PR.
- Web3Signer key #3 loaded alongside keys #1 and #2 (three concurrent
  distinct keys in the shared signer tier).
- Teku VC runtime launch verified with unreachable-signer contract before
  merge (loaded exactly one requested key, started doppelganger, exposed
  `validator_local_validator_count 1`, could not sign).
- Validator 03 cleared doppelganger detection and reports
  `active_ongoing`; attributable attestations have been observed on the
  live cluster. Current cadence and running totals are reported on the
  [live portal](https://g.j2d3.com). A per-validator evidence snapshot
  under [`docs/evidence/`](../evidence/) is a follow-up.

## Problems encountered and corrections

- **`--initial-state` misuse** (#109 review by Codex): flag documents a
  direct SSZ URL; corrected to `--checkpoint-sync-url`.
- **`--metrics-host-allowlist` missing** (#109 review): Teku's default
  localhost-only Host allowlist rejected Prometheus scrapes; added `*`
  (NetworkPolicy on port 8008 is the L4 boundary).
- **`libp2p_peers` vs `beacon_peer_count`** (#122 review): metric-name
  correction after Codex verified Teku's actual `/metrics` output.
- **Ephemery config fetch** (#115): Teku's built-in Ephemery preset tried to
  fetch bootnodes over HTTPS — signer NetworkPolicy denied. Fixed to mount
  the pinned generation config as a local ConfigMap so no fetch is needed.
- **ConfigMap namespace** (#116): the generated ConfigMap needed
  `metadata.namespace: signing` via an overlay patch (Kustomize's
  configMapGenerator emits namespaceless objects; Flux rejects the ambiguity).
- **`configure-validator` init container is Lighthouse-only** (#132): guarded
  by `if eq .Values.consensusClient "lighthouse"` so Teku's Deployment skips
  the Lighthouse validator-definitions ConfigMap.
- **Teku's `/eth/v2/debug/beacon/states/genesis` returns 404** (#132): the
  Lighthouse-only debug endpoint doesn't exist on Teku's beacon; the Teku
  VC's genesis-fetch init container skips the SSZ request and relies on
  `--network=<file>` for state.

## Remaining unqualified

- Long-term attestation effectiveness and proposal duty timing.
- Any lifecycle transition (stop → reactivate → archive).

## References

- Chart adapter PR (Teku beacon): [#109](https://github.com/j2d3/eth-validator-platform/pull/109)
- Chart adapter PR (Teku VC): [#132](https://github.com/j2d3/eth-validator-platform/pull/132)
- Catalog / activation PR: [#122](https://github.com/j2d3/eth-validator-platform/pull/122) (non-signing) + [#136](https://github.com/j2d3/eth-validator-platform/pull/136) (signing flip)
- Ephemery config offline mount: [#115](https://github.com/j2d3/eth-validator-platform/pull/115)
- Third-key infrastructure PRs: [#133](https://github.com/j2d3/eth-validator-platform/pull/133), [#135](https://github.com/j2d3/eth-validator-platform/pull/135)
