# Besu + Teku

**First JVM-EL + JVM-CL composition.** Both Besu (Hyperledger / LF
Decentralized Trust) and Teku (documented as an open-source consensus
client on Consensys-hosted docs) run on the JVM. Same-Pod colocation of
two heap-sensitive JVM processes is the durable operational data point
this pair adds. Chart adapters already exist — Besu EL in #137, Teku CL
in #109 — so activation is catalog-only.

## Why this pair

- **JVM-on-JVM co-scheduling.** Both Besu and Teku are JVM processes.
  Same-Pod colocation exercises whether two heap-sensitive JVMs share
  a node's memory reservation cleanly. (Neither is signing on this
  pair, so the Web3Signer signer-tier JVM heap story from
  [`web3signer-and-slashing-protection`](../components/web3signer-and-slashing-protection.md#jvm-heap-sizing-the-scrypt-story)
  is not on the critical path here.)
- **Adapter-composition confirmation.** Besu shipped alone against
  Lighthouse in the CI matrix but had never composed against Teku in a
  rendered release. This is the first end-to-end render. A dedicated
  contract test (`BesuTekuCompositionRenderTests` in
  `tests/test_chart_besu_adapter_contracts.py`) renders the composition
  and asserts the StatefulSet has both the Besu EL command/image and
  the Teku CL command/image.
- **Runtime exposure of Besu.** Besu had a chart adapter after #137
  but no rendered release; this catalog PR turns that adapter into a
  live workload target for the first time.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-besu-teku.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-besu-teku.yaml`
- **Synthetic ValidatorIdentity** (non-signing): `validator-ephemery-162-synthetic-besu-teku`
- **Node pair name**: `pair-ephemery-162-synthetic-besu-teku`

## Topology

- `execution`: Besu (image from #137's `values.yaml` chart adapter,
  digest-pinned).
- `consensus`: Teku beacon node (same image as [`geth-teku`](geth-teku.md)).
- `validator`: none — pair is non-signing by design per issue #130's
  non-goals.

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle. Both adapter
modes declared as `artifact-bundle` in the network profile's `clients`
map. Besu was added to the schema's `clients.properties` and the
network profile's `clients` map as part of this catalog PR.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3` (its own; separate from other
  pairs' PVCs because `nodePairRef` differs).
- Consensus PVC: 20 GiB encrypted `gp3`.
- No validator PVC (chart renders it only when `validator.enabled=true`,
  which stays `false` on this non-signing pair).

## Engine API wiring

Besu's `--engine-rpc-*` connects to Teku's `--ee-endpoint` exactly as
Geth's `--authrpc.*` does for Lighthouse. The Engine API is
EL/CL-agnostic; the Engine JWT (`/jwt/jwt.hex`) is shared by both
containers in the Pod via the same projected volume.

## Client-specific command-line adaptations

Besu is documented in the shared component pages alongside the other
EL adapters; Teku is documented in [`geth-teku`](geth-teku.md).
Besu+Teku exercises the union with `BesuTekuCompositionRenderTests`
guarding the dispatch.

## Metric normalization

**Status: configured but runtime-unverified.**

- Besu is *configured* to contribute `ethereum_blockchain_height` and
  `ethereum_peer_count` (from #137's chart adapter). These names have
  not yet been observed on a running Besu Pod; the first live scrape
  will confirm or expose them, following the exact pattern that
  surfaced Erigon's `chain_head_block` gap (fixed in #148 by observing
  that Erigon exposes `sync` with a `stage` label instead).
- Teku is *observed* to contribute `beacon_head_slot`, `beacon_slot`,
  `beacon_epoch`, `beacon_finalized_epoch`, `libp2p_peers` (verified
  on the three prior Teku pairs).

The recording rules degrade to empty series for unknown names rather
than render errors (per PRD §12.5), so any Besu metric gap surfaces as
missing panels rather than broken dashboards — treat missing series as
"unsupported/not collected, not zero" and file a follow-up.

## Non-signing qualification

Live sync qualification is the point of this pair. Test contract
asserts that this release, plus the other five Ephemery pairs, all
render with the same EKS-required patches (`valuesFiles`, dev
telemetry, `aws-engine-secrets` Engine JWT) — a missing overlay patch
on any release would fail CI.

## Signing qualification

**Non-signing by design.** No validator identity is bound to this
assignment. Enabling signing would require:

1. A registered (non-synthetic) `ValidatorIdentity` with a deposited
   public key and its `signingSecretRef`.
2. Web3Signer projection extended to hold that key.
3. A future Teku VC assignment wiring the validator client on this
   specific pair (the Teku VC adapter from #132 supports this pattern
   — no new chart work would be needed).

## References

- Besu EL chart adapter: [#137](https://github.com/j2d3/eth-validator-platform/pull/137)
- Teku CL chart adapter: [#109](https://github.com/j2d3/eth-validator-platform/pull/109)
- Erigon metric-observation pattern (precedent for the "configured
  but runtime-unverified" framing above):
  [#148](https://github.com/j2d3/eth-validator-platform/pull/148)
- Umbrella issue: [#130](https://github.com/j2d3/eth-validator-platform/issues/130)
