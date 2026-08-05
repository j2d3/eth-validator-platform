# Reth + Teku

**Completes the original 2×2 matrix.** Uses only existing chart adapters —
Reth (from [`reth-lighthouse`](reth-lighthouse.md)) and Teku (from
[`geth-teku`](geth-teku.md)) — so the interesting variable is whether two
independently built single-variable adapters actually compose.

## Why this pair

The Reth adapter had been proven against Lighthouse; the Teku adapter had
been proven against Geth. Neither had ever composed. Reth+Teku exercises:

1. The chart's per-EL and per-CL dispatch fires simultaneously on non-Geth
   and non-Lighthouse selections.
2. The same Ephemery bundle serves both a Rust EL and a Java CL without
   client-specific special-casing at the bundle layer.
3. Neither adapter accidentally depends on a sibling default from the other
   client family.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-reth-teku.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-reth-teku.yaml`
  (kept its historical name after activation to preserve the running
  StatefulSet, PVC, and Web3Signer projection identities)
- **ValidatorIdentity**: `validator-ephemery-162-04` (registered,
  non-synthetic, distinct pubkey with its own identity-addressed
  Secrets Manager container)
- **Node pair name**: `pair-ephemery-162-synthetic-reth-teku`

## Topology

- `execution`: Reth (same image as [`reth-lighthouse`](reth-lighthouse.md)).
- `consensus`: Teku beacon node (same image as [`geth-teku`](geth-teku.md)).
- `validator`: Teku VC running against the shared Web3Signer with the
  validator-04 key (see [`geth-teku`](geth-teku.md) for the same VC
  adapter shape).

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle. Both adapter modes
declared as `artifact-bundle` in the network profile's `clients` map.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3` (its own; separate from
  reth-lighthouse's PVC because the `nodePairRef` differs).
- Consensus PVC: 20 GiB encrypted `gp3`.
- Validator PVC: 5 GiB encrypted `gp3`, rendered when
  `validator.enabled=true` and mounted by the Teku VC at `/validator-data`.
  It is disposable and not authoritative for slashing safety: the VC runs
  with `--validators-external-signer-slashing-protection-enabled=false`, so
  Web3Signer + RDS remains the sole authoritative slashing-protection store.

## Engine API wiring

Reth's `--authrpc.*` connects to Teku's beacon exactly as it would to
Lighthouse's. The Engine API is EL/CL-agnostic; the Engine JWT
(`/jwt/jwt.hex`) is shared by both containers in the Pod via the same
projected volume.

## Client-specific command-line adaptations

Same as documented in [`reth-lighthouse`](reth-lighthouse.md) (EL) and
[`geth-teku`](geth-teku.md) (CL beacon). Reth+Teku exercises the union.

## Metric normalization

- Reth contributes `reth_blockchain_tree_canonical_chain_height`,
  `reth_network_connected_peers`. `headHeader` omitted (per Reth's
  documented PRD §12.5 rationale).
- Teku contributes `beacon_head_slot`, `beacon_slot`, `beacon_epoch`,
  `beacon_finalized_epoch`, `libp2p_peers`.

The PrometheusRule builds `validator_platform_*` unions across all declared
adapters; a Reth+Teku pair reports both Reth's execution series and Teku's
consensus series through the same normalized names, filtered by
`execution_client="reth"` and `consensus_client="teku"` labels.

## Non-signing qualification

Live sync completed on both sides. Test contract asserts that this pair,
plus the other three then-existing Ephemery pairs, all render with the same
EKS-required patches (`valuesFiles`, dev telemetry, `aws-engine-secrets`
Engine JWT) — a missing overlay patch on any release would fail CI.

## Signing qualification

**Update after PR #144:** this pair is now the signing home for
validator 04. The synthetic identity was replaced with
`validator-ephemery-162-04` (registered, distinct pubkey and
`signingSecretRef`); Web3Signer projection carries the fourth key via
the shared ExternalSecret (PR #141); Teku VC runs against the shared
signer via the adapter from #132.

- Validator 04 cleared doppelganger detection and reports
  `active_ongoing`; attributable attestations have been observed on the
  live cluster. Current cadence and running totals are reported on the
  [live portal](https://g.j2d3.com). A per-validator evidence snapshot
  under [`docs/evidence/`](../evidence/) is a follow-up.
- Same guarantees as validator 03 on Geth+Teku (see
  [`geth-teku`](geth-teku.md)): remote-signer via Web3Signer + RDS,
  disjoint pubkey, distinct identity-addressed Secrets Manager
  container, four safety flags true on the assignment.

## Problems encountered and corrections

- **EKS overlay patch omission** ([#122 review by Codex](https://github.com/j2d3/eth-validator-platform/pull/122#issuecomment-5183659155)):
  the initial Teku catalog PR only patched the Geth+Lighthouse and
  Reth+Lighthouse releases; the new Geth+Teku release rendered without EKS
  values and would have used local-kind defaults on EKS. Same class of
  omission applied here — every added Ephemery release must receive the
  three EKS patches, enforced by the per-release `subTest` loop in
  `test_signing_node_layer_waits_for_signer_application`.

## Remaining unqualified

- Long-term sync behavior.
- Long-term signing behavior (activation as of #144; observed
  attestations to date do not yet cover a full activation-to-exit cycle
  on this pair).

## References

- Catalog PR (non-signing): [#126](https://github.com/j2d3/eth-validator-platform/pull/126)
- Chart adapters used: Reth [#93](https://github.com/j2d3/eth-validator-platform/pull/93) and Teku [#109](https://github.com/j2d3/eth-validator-platform/pull/109)
- Teku VC adapter: [#132](https://github.com/j2d3/eth-validator-platform/pull/132)
- Signing activation PR (validator 04): [#144](https://github.com/j2d3/eth-validator-platform/pull/144)
- Web3Signer keystore projection (validator 04): [#141](https://github.com/j2d3/eth-validator-platform/pull/141)
- Empty Secrets Manager container declaration (validator 04): [#139](https://github.com/j2d3/eth-validator-platform/pull/139)
