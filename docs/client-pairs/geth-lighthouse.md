# Geth + Lighthouse

**Baseline vertical slice.** Every other pair is a controlled variation on
this one.

## Why this pair

Geth (`ethereum/client-go`) is the reference execution client — the largest
mainnet share, the most-documented Engine API implementation, and the most
mature snap-sync path. Lighthouse (`sigp/lighthouse`) is a Rust consensus
client with a well-documented remote-signing integration through
[Web3Signer](https://docs.web3signer.consensys.io/). Choosing this pair as
the first slice separated "does the chart work end-to-end?" from "does client
X's specific quirk break things?" — later pairs isolate the client-quirk
question one variable at a time.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-geth-lighthouse.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic.yaml`
- **Registered ValidatorIdentity** (signing): `validator-ephemery-162-01`
- **Node pair name**: `pair-ephemery-162-synthetic`

## Topology

Two containers in one StatefulSet Pod, one validator client in a separate
Deployment:

- `execution`: Geth (`ethereum/client-go:v1.17.5`) — full sync on the EKS
  Ephemery profile (avoids the snap-pivot failure surface observed in the
  first restart exercise).
- `consensus`: Lighthouse beacon node (`sigp/lighthouse:v8.2.1`) — same
  image as the validator client.
- `validator`: Lighthouse validator client (same image), separate Deployment
  with its own encrypted PVC.

## Network configuration

Uses the `ephemery-162` network profile with the digest-pinned
`ephemery-genesis` bundle (`sha256:478ca7…8967ee2fb`). The chart's
artifact-mode init containers fetch and verify the bundle before either
client starts.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3` on EKS.
- Consensus PVC: 20 GiB encrypted `gp3`.
- Validator PVC: 5 GiB encrypted `gp3` (Lighthouse VC's local SQLite
  slashing-history bookkeeping — Web3Signer + RDS is the canonical authority).
- Restart is stateful for both node PVCs; the validator PVC is disposable
  because Web3Signer holds the authoritative history.

## Engine API and remote-signer wiring

- Lighthouse beacon: `--execution-endpoint=http://127.0.0.1:8551`,
  `--execution-jwt=/jwt/jwt.hex`.
- Lighthouse VC: `--beacon-nodes=http://<pair-service>:5052`,
  `--disable-slashing-protection-web3signer` (delegates enforcement to
  Web3Signer + RDS), `--enable-doppelganger-protection`.

## Metric normalization

Lighthouse and Geth expose distinct metric namespaces; the chart's
PrometheusRule builds `validator_platform_*` recording rules by unioning
per-client series. Lighthouse-specific:

- `beacon_head_state_slot`, `slotclock_present_slot`, `slotclock_present_epoch`,
  `beacon_head_state_finalized_epoch`, `sync_peers_per_status` (peer counts
  are per-status buckets, summed).
- `vc_validators_enabled_count` from the separate VC (runtime-verified in
  #124 after the initial rule referenced a non-existent metric name).

## Non-signing qualification

Live sync completed on Ephemery-162 with the observed generation identity
matching the pinned bundle. Pair was Ready with advancing execution and
consensus heads before any signing gate was considered.

## Signing qualification

- Deposit: 32 tETH into Ephemery deposit contract, validator index `30201`,
  activation epoch `1060`.
- First attributable attestation: slot `33927`.
- Web3Signer key #1 loaded; Lighthouse VC completed doppelganger detection
  before starting duties.

Evidence: [2026-08-04-first-signing-validator.md](../evidence/2026-08-04-first-signing-validator.md).

## Problems encountered and corrections

- **Genesis-state fetch**: Lighthouse v8.2.1 requires `genesis.ssz` in its
  `--testnet-dir`; the file is 6.9 MB, too large for a ConfigMap. The chart
  fetches it from the pair's own beacon at startup and verifies its
  `genesis_time` + `genesis_validators_root` against the pinned network
  identity (see #120).
- **JVM heap sizing for Web3Signer**: initial keystore decrypt OOM'd at
  ~248 MiB (JVM's default 25% of the 1 GiB container limit). Fixed to
  explicit 640 MiB max heap with 384 MiB native headroom (#117).
- **`validator_platform_validator_enabled` metric**: initial recording rule
  referenced non-existent `validator_enabled_count`. Corrected to Lighthouse
  VC's actual `vc_validators_enabled_count` (#124).

## Remaining unqualified

- Long-term attestation effectiveness.
- Block proposal and sync-committee duties (deposit happened but proposals
  are epoch-scheduled — not yet observed).
- Stop / reactivate / archive / client migration for the running identity.
- Slashing-history export/import and PITR drills.

## References

- Chart PR: (baseline; the ethereum-node chart itself is the origin)
- Signing activation PR: [#118](https://github.com/j2d3/eth-validator-platform/pull/118)
- Validator runtime init PR: [#120](https://github.com/j2d3/eth-validator-platform/pull/120)
- Metric-name correction: [#124](https://github.com/j2d3/eth-validator-platform/pull/124)
- Evidence: [first Web3Signer-backed attestation](../evidence/2026-08-04-first-signing-validator.md)
