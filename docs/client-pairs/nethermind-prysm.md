# Nethermind + Prysm

**First rendered release exercising the Prysm CL adapter.** Chart
adapter landed in #163 with a pre-exec Ephemery→Prysm config
derivation (`grep-Ev` strips `EPHEMERY_RESET_PERIOD` and
`NUMBER_OF_COLUMNS`; `printf` appends `GLOAS_FORK_VERSION 0x8000101b`
and `GLOAS_FORK_EPOCH 18446744073709551615`; `grep-Eq` fail-closes on
upstream shape drift) and a derived-`presentEpoch` recording rule
(`floor(max(beacon_clock_time_slot) / 32)`). This catalog PR turns
that adapter into a live workload target for the first time, paired
with the runtime-verified Nethermind EL from #156/#161/#162 so the
observed variable is the Go/prysmatic-labs beacon-chain runtime alone.

## Why this pair

- **Fifth distinct CL runtime.** The four earlier CLs are Rust
  (Lighthouse), Java (Teku), Nim (Nimbus). Prysm is Go. The runtime
  characteristics worth watching are the Go GC behavior under
  attestation load and how the derived-`presentEpoch` recording-rule
  path scrapes on live series (Prysm doesn't publish a direct
  present-epoch gauge, so the aggregate finality-lag series comes
  from `floor(beacon_clock_time_slot / 32)` rather than a scraped
  counter).
- **Runtime-verified Nethermind EL.** Nethermind has been through
  three post-adapter corrections (#161 bootnode normalization and
  writable-path routing to `/tmp`; #162 peer-count metric swap to
  `ethereum_peer_count`). Pairing Prysm with Nethermind isolates the
  CL variable — anything anomalous is Prysm-specific.
- **Ephemery config derivation is chart-side, not bundle-side.**
  Prysm rejects two Ephemery-generator keys (`EPHEMERY_RESET_PERIOD`,
  `NUMBER_OF_COLUMNS`) and inherits a mainnet Gloas fork-version
  collision. Rather than fork the Ephemery bundle, the chart's
  `prysmRunCommand` derives `/tmp/prysm-config.yaml` pre-exec.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-nethermind-prysm.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-nethermind-prysm.yaml`
- **Synthetic ValidatorIdentity** (non-signing): `validator-ephemery-162-synthetic-nethermind-prysm`
- **Node pair name**: `pair-ephemery-162-synthetic-nethermind-prysm`

## Topology

- `execution`: Nethermind 1.39.2 (image + digest from #156's
  values.yaml, index-pinned).
- `consensus`: Prysm v7.1.8 beacon-chain (image + digest from #163's
  values.yaml, pinned as
  `gcr.io/prysmaticlabs/prysm/beacon-chain:v7.1.8@sha256:31239807...`).
- `validator`: none — pair is non-signing by design per issue #130's
  non-goals, and the chart has no Prysm VC adapter yet
  (`signingAllowed: false` on the ServiceProfile guards this at
  catalog-validation time).

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle. Nethermind
consumes `chainspec.json` via `--Init.ChainSpecPath` (from #156's
bundle mapping). Prysm consumes a derived `/tmp/prysm-config.yaml`
built pre-exec from the bundle's `config.yaml`, plus `genesis.ssz`
directly and repeated `--bootstrap-node=<ENR>` flags one per line from
`boot_enr.txt`.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3` (its own; separate from other
  pairs' PVCs because `nodePairRef` differs).
- Consensus PVC: 20 GiB encrypted `gp3`.
- No validator PVC (chart renders it only when `validator.enabled=true`,
  which stays `false` on this non-signing pair).
- Restart-safe execution init: #156's Nethermind init state-machine
  writes the platform identity marker before any mkdirs; #161 pinned
  static/trusted-node paths to `/tmp` so read-only-root violations
  cannot recur.
- Prysm `--datadir=/data/prysm` — Prysm's own state directory sits
  alongside any future adjacent components. The derived
  `/tmp/prysm-config.yaml` is regenerated at every Pod start from the
  read-only bundle, so no Prysm-side state escapes into the durable
  PVC.

## Engine API wiring

Nethermind's `--JsonRpc.EngineHost + --JsonRpc.EnginePort=8551` speaks
the standard Engine API. Prysm's `--execution-endpoint=http://
127.0.0.1:8551` addresses the localhost side of the paired Pod. The
Engine JWT (`/jwt/jwt.hex`) is shared by both containers via the same
projected volume, sourced from Secrets Manager via the AWS SecretStore
per the EKS overlay.

## Prysm-specific command-line adaptations

Per #163's chart adapter:

- **Config derivation pre-exec.** The consensus container runs
  `set -eu` and then derives `/tmp/prysm-config.yaml` from the
  bundle's `config.yaml`: `grep -Eq` refuses to run if the source
  already contains any `GLOAS_FORK_(VERSION|EPOCH):` line;
  `grep -Ev` strips `EPHEMERY_RESET_PERIOD:` and `NUMBER_OF_COLUMNS:`;
  `printf` appends `GLOAS_FORK_VERSION: 0x8000101b` and
  `GLOAS_FORK_EPOCH: 18446744073709551615`.
- **Repeated `--bootstrap-node=<ENR>` flags.** Prysm accepts one flag
  per ENR, not a single CSV value. The `prysmRunCommand` reads the
  bundle's line-delimited `boot_enr.txt` and emits one flag per line;
  a defensive test asserts the CSV shape is never produced.
- **`--http-host=0.0.0.0 --http-port=5052`** for the REST API,
  **`--monitoring-host=0.0.0.0 --monitoring-port=8008`** for the
  Prometheus surface. gRPC (`--rpc-host=127.0.0.1`) stays on loopback
  since no in-cluster consumer needs it.
- **`--accept-terms-of-use`** required by Prysm on non-mainnet
  networks.

## Metric normalization

**Prysm does not publish a direct present-epoch gauge.** Its
`/metrics` surface exposes `beacon_head_slot`,
`beacon_clock_time_slot`, `beacon_finalized_epoch`, and
`connected_libp2p_peers` (verified by Codex on v7.1.8 on 2026-08-05
during the #163 chart-adapter review). Rather than declare a
nonexistent `presentEpoch` name, the chart adapter declares
`presentEpochDivisor: 32` and the shared
`validator_platform_consensus_finality_lag_epochs` recording rule
uses a per-CL branch to compute `floor(max(presentSlot) / 32) -
max(finalizedEpoch)` for Prysm; the direct-metric branch is unchanged
for Lighthouse/Teku/Nimbus.

Test contract asserts that this release, plus the other seven
Ephemery pairs, all render with the same EKS-required patches
(`valuesFiles`, dev telemetry, `aws-engine-secrets` Engine JWT).

## Non-signing qualification gates

The runtime qualification for this pair after Flux reconciles it
should record observable evidence for each of:

- **Pod start**: both containers become Ready (2/2), Prysm's pre-exec
  derivation writes `/tmp/prysm-config.yaml` without triggering the
  fail-closed guard, no restart-loop.
- **Peers**: `validator_platform_consensus_peers{consensus_client=
  "prysm"}` reports a non-zero value via `connected_libp2p_peers`;
  Nethermind execution peers via `ethereum_peer_count` also non-zero.
- **Slot movement**: `validator_platform_consensus_head_changes_15m{
  consensus_client="prysm"}` advances over a 15-minute window via
  `beacon_head_slot`.
- **Derived-epoch finality series is non-empty**: the recording rule's
  `floor(...beacon_clock_time_slot... / 32)` branch must render live
  data — if it doesn't, `beacon_clock_time_slot` isn't being scraped
  or the recording rule regressed; either failure surfaces as an
  empty finality-lag series (which is the exact regression #163's
  `test_finality_lag_expr_derives_epoch_from_prysm_slot` locks
  against at chart-render time).
- **No divide-by-zero or empty selectors**: the `oneOf` invariant on
  `consensusMetricsMap` protects against a future map that declares
  neither `presentEpoch` nor `presentEpochDivisor`, but the runtime
  probe should also confirm no NaN/empty tiles land in the finality
  panel.

## Signing qualification

**Non-signing by design.** `signingAllowed: false` on the
ServiceProfile because no Prysm VC chart adapter exists yet (same
fail-closed pattern as `dedicated-geth-nimbus`). A future Prysm VC
adapter analogous to Teku's #132 would need to land before any
signing pair against Prysm is considered.

## References

- Prysm CL chart adapter: [#163](https://github.com/j2d3/eth-validator-platform/pull/163)
- Nethermind EL chart adapter: [#156](https://github.com/j2d3/eth-validator-platform/pull/156)
- Nethermind bootnodes + writable paths fix: [#161](https://github.com/j2d3/eth-validator-platform/pull/161)
- Nethermind `ethereum_peer_count` observed-metric swap: [#162](https://github.com/j2d3/eth-validator-platform/pull/162)
- Nethermind+Lighthouse baseline pair: [#160](https://github.com/j2d3/eth-validator-platform/pull/160)
- Umbrella issue: [#130](https://github.com/j2d3/eth-validator-platform/issues/130)
