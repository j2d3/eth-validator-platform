# Geth + Nimbus

**First Nim-language CL on a proven EL.** The Nimbus chart adapter
landed in #138 (with runtime-verified `--external-beacon-api-url`,
`--bootstrap-file` pointed at line-delimited ENRs, and `--el` for the
EE endpoint). This pair composes it against the well-exercised Geth EL.

## Why this pair

- **Nimbus is architecturally unusual** among the major CLs — Nim
  language, designed for embedded/low-resource hardware. Its resource
  profile and metric surface are different from JVM (Teku) or Rust
  (Lighthouse) CLs.
- **Uses the well-exercised EL side.** Pairing with Geth (already
  running in three other Ephemery pairs) isolates the variable to the
  CL adapter.
- **Runtime confirmation of #138's flag corrections.** #138 shipped
  three flag corrections after Codex's live-cluster review — this pair
  is the first rendered release that exercises the corrected command
  shape end-to-end.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-geth-nimbus.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-geth-nimbus.yaml`
- **Synthetic ValidatorIdentity** (non-signing): `validator-ephemery-162-synthetic-geth-nimbus`
- **Node pair name**: `pair-ephemery-162-synthetic-geth-nimbus`

## Topology

- `execution`: Geth (same image as [`geth-lighthouse`](geth-lighthouse.md)).
- `consensus`: Nimbus beacon node (image from #138's `values.yaml`,
  digest-pinned; `statusim/nimbus-eth2`).
- `validator`: none — pair is non-signing by design per #130's
  non-goals.

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle. Both adapter
modes declared as `artifact-bundle` in the network profile's `clients`
map. Nimbus was added to the schema's `clients.properties` and the
network profile's `clients` map as part of this catalog PR.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3`.
- Consensus PVC: 20 GiB encrypted `gp3`.
- No validator PVC (chart renders it only when `validator.enabled=true`,
  which stays `false` on this non-signing pair).

## Engine API wiring

Geth's `--authrpc.*` connects to Nimbus's beacon via the shared Engine
JWT (`/jwt/jwt.hex`) mounted in both containers.

## Nimbus-specific command-line adaptations

Per #138's runtime-corrected adapter:

- `--network=/network/files` — Nimbus takes the network **directory**
  (not a single file), unlike Lighthouse's `--testnet-dir`.
- `--external-beacon-api-url=<checkpoint-sync>` — checkpoint-adjacent
  BN wiring at normal startup (not the `trustedNodeSync` subcommand
  path, which is only accepted by that specific one-shot mode and
  would 403 the normal startup command).
- `--bootstrap-file=/network/files/boot_enr.txt` — Nimbus requires
  line-delimited ENRs; YAML list markers fail with "Unknown bootstrap
  file format", so the file pointer is `consensusBootnodesText`, not
  `consensusBootnodes`.
- `--el=http://127.0.0.1:8551` — current Nimbus flag; `--web3-url`
  is a hidden legacy alias and is deliberately not emitted.

## Metric normalization

**Status: configured but runtime-unverified.**

- Geth is *observed* to contribute `chain_head_header`, `chain_head_block`,
  and execution peer count metrics (verified on the three prior Geth
  pairs).
- Nimbus is *configured* to contribute `libp2p_peers` from #138's
  PrometheusRule adapter registration. The Nimbus chart contract in
  #138 explicitly does not prove runtime metric-name accuracy — first
  live scrape on this pair is the qualification gate, following the
  same pattern that surfaced Erigon's `chain_head_block` gap (fixed
  in #148 by observing `sync{stage=…}` instead).

The PrometheusRule builds `validator_platform_*` unions across all
declared adapters, filtered by `execution_client="geth"` and
`consensus_client="nimbus"` labels. Missing series degrade to empty
per PRD §12.5 ("unsupported/not collected, not zero"), not render
errors.

## Non-signing qualification

**Status: desired-state and render contracts configured; live sync
qualification is the next gate.** This is the first rendered release
exercising the Nimbus CL adapter — until Flux reconciles it on EKS
and the beacon Pod scrapes actual metrics, "the chart renders correctly"
is the strongest claim available. Test contract asserts that this
release, plus the other five Ephemery pairs, render with the same
EKS-required patches (`valuesFiles`, dev telemetry, `aws-engine-secrets`
Engine JWT); missing overlay patches on any release would fail CI.

## Signing qualification

**Non-signing by design.** Enabling signing on this pair would require
a validator client — Nimbus ships one, but the chart doesn't currently
have a Nimbus VC adapter. If signing becomes desired here, that adapter
work is a follow-up (analogous to #132 for Teku).

## References

- Nimbus CL chart adapter: [#138](https://github.com/j2d3/eth-validator-platform/pull/138)
- Geth EL: shipped in the original chart, exercised by the baseline
  Geth+Lighthouse pair.
- Umbrella issue: [#130](https://github.com/j2d3/eth-validator-platform/issues/130)
