# Nethermind + Prysm

This is the ninth active Ephemery pair and the first release to run the Prysm
adapter. The Nethermind EL adapter landed in
[#156](https://github.com/j2d3/eth-validator-platform/pull/156) and has
been exercised live since the
[Nethermind+Lighthouse](nethermind-lighthouse.md) activation
([#160](https://github.com/j2d3/eth-validator-platform/pull/160)); the
Prysm CL adapter landed in
[#163](https://github.com/j2d3/eth-validator-platform/pull/163). The
catalog activation landed in #164. Its first EKS start exposed and corrected
two runtime assumptions: Prysm's image does not include `grep` (#165), and
Nethermind creates its databases directly below `/data` rather than below a
`nethermind_db` directory (#166).

## Why this pair

- **Prysm coverage.** Prysm is the fourth CL adapter. Its config-derivation
  step (below) differs from the other CL adapters because it rewrites the
  verified bundle configuration before exec.
- **Reused Nethermind path.** Nethermind's command shape, data layout, and
  metric names were already observed on the Nethermind+Lighthouse pair.
  This pair exercises the new Prysm path while keeping that execution-client
  path constant.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-nethermind-prysm.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-nethermind-prysm.yaml`
- **Synthetic ValidatorIdentity** (non-signing): `validator-ephemery-162-synthetic-nethermind-prysm`
- **Node pair name**: `pair-ephemery-162-synthetic-nethermind-prysm`

This is the ninth active Ephemery-162 pair (four signing, five
non-signing).

## Topology

- `execution`: Nethermind 1.39.2 (digest-pinned image from #156's
  `values.yaml`, same as [`nethermind-lighthouse`](nethermind-lighthouse.md)).
- `consensus`: Prysm beacon node
  `gcr.io/prysmaticlabs/prysm/beacon-chain:v7.1.8` (digest-pinned in
  #163's `values.yaml`; entrypoint `/beacon-chain`).
- `validator`: none — pair is non-signing by design per #130's
  non-goals.

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle every other pair
consumes. Nethermind reads `chainspec.json` via `--Init.ChainSpecPath`;
Prysm reads a **derived** copy of `config.yaml` (see below) plus
`genesis.ssz` and the line-delimited `boot_enr.txt`. Both clients are
declared `mode: artifact-bundle` in the network profile's `clients` map
— `prysm` was added there by #163, so this activation needs no network
profile or schema change.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3` (its own; separate from the
  other Nethermind pair's PVC because `nodePairRef` differs).
- Consensus PVC: 20 GiB encrypted `gp3`. Prysm's `--datadir=/data/prysm`
  targets a child of the mount rather than the mount root, the same
  ownership accommodation Nimbus needed in #154.
- No validator PVC (the chart renders it only when
  `validator.enabled=true`, which stays `false` on this pair).
- Restart-safe execution init: the Nethermind init helper writes the platform
  identity marker before any mkdirs, treats marker-only and
  marker-plus-keystore claims as resumable first starts, and uses the
  runtime-observed `/data/metadata` directory as the initialized-data
  sentinel. Wrong-network, unmarked non-empty, and marked-but-unrecognized
  claims fail closed. #166 corrected the sentinel and exercised a real
  retained-PVC restart.

## Engine API wiring

Nethermind's `--JsonRpc.EngineHost` / `--JsonRpc.EnginePort=8551` speaks
the standard Engine API; Prysm's
`--execution-endpoint=http://127.0.0.1:8551` addresses the loopback side
of the paired Pod. The Engine JWT (`/jwt/jwt.hex`) is shared by both
containers through the same projected volume, sourced from Secrets
Manager via the AWS SecretStore per the EKS overlay and per-pair scoped
by `fullnameOverride` (`pair-<validator>-engine-jwt`).

## Prysm-specific command-line adaptations

Per #163's chart adapter. The distinguishing one is that Prysm is the
only client in the fleet that cannot consume a bundle file as shipped:

- **Pre-exec config derivation.** The consensus container derives
  `/tmp/prysm-config.yaml` from the bundle's `config.yaml` before
  `exec /beacon-chain`, because Prysm rejects `EPHEMERY_RESET_PERIOD`
  and `NUMBER_OF_COLUMNS` with an unknown-key failure and then inherits
  a mainnet Gloas fork-version collision. The derivation strips those
  two keys and appends `GLOAS_FORK_VERSION: 0x8000101b` and
  `GLOAS_FORK_EPOCH: 18446744073709551615`. It writes to `/tmp` because
  `/network/files` is a read-only projection of the bundle, and it runs
  *after* the network-artifact-loader init container has verified the
  bundle SHA — the derivation never widens what the digest already
  covers. #165 replaced the initial `grep` implementation with POSIX shell
  built-ins after the pinned Prysm image proved to contain `/bin/sh` but no
  `grep`, `sed`, or `awk`.
- **Fail-closed on shape drift.** If the source `config.yaml` already
  carries a `GLOAS_FORK_(VERSION|EPOCH):` line, the container exits
  non-zero with "refusing to append" rather than silently overriding an
  upstream change no operator reviewed. A reshaped bundle stops the
  pair; it does not start it on unreviewed consensus parameters.
- **Repeated `--bootstrap-node` flags, not CSV.** Prysm accepts one
  flag per ENR. The adapter loops over `boot_enr.txt` and emits one
  `--bootstrap-node=<ENR>` per line, then asserts the assembled argument
  string is non-empty. This differs from Lighthouse's and Teku's
  comma-joined single value — passing CSV here parses as one malformed
  ENR (the same class of defect #161 fixed for Nethermind's enodes).
- **Split HTTP/gRPC surfaces.** `--http-host=0.0.0.0 --http-port=5052`
  exposes the REST API and `--monitoring-host=0.0.0.0
  --monitoring-port=8008` exposes `/metrics` on the Pod IP, while gRPC
  stays on `--rpc-host=127.0.0.1` because no in-cluster consumer needs
  it.
- `--genesis-state=/network/files/genesis.ssz`, `--datadir=/data/prysm`,
  `--jwt-secret=/jwt/jwt.hex`, `--checkpoint-sync-url` from the network
  profile, `--p2p-local-ip=0.0.0.0` with TCP/UDP 9000 and QUIC 9001,
  and `--accept-terms-of-use`.

Nethermind's adaptations are unchanged from
[`nethermind-lighthouse`](nethermind-lighthouse.md): `--config=none`,
`--Init.ChainSpecPath`, `--Init.BaseDbPath=/data`, static/trusted-node
files on `/tmp`, `--KeyStore.KeyStoreDirectory=/data/keystore`, and
comma-joined `--Network.Bootnodes` normalized per #161.

## Metric normalization

**Status: both clients and all normalized metrics below are observed on the
active EKS pair.**

- Nethermind contributes `nethermind_blocks` and `ethereum_peer_count`,
  both *observed* on the live Ephemery Pod running the pinned 1.39.2
  image (#162 replaced the originally configured `nethermind_peers`
  with the aggregate that actually exists). `executionMetricsPath` sends
  Prometheus to `/metrics`, not Geth's
  `/debug/metrics/prometheus`.
- Prysm contributes `beacon_head_slot`,
  `beacon_clock_time_slot`, `beacon_finalized_epoch`, and
  `connected_libp2p_peers` — note the `connected_` prefix, distinct from
  the Teku/Nimbus `libp2p_peers` naming. All four names were scraped from
  the live v7.1.8 Pod after #166.
- **Derived present-epoch.** Prysm publishes no direct present-epoch
  gauge, so its metric map declares `presentEpochDivisor: 32` instead of
  `presentEpoch`, and the recording rule computes
  `floor(max(beacon_clock_time_slot) / 32) - finalized` for the
  `validator_platform_consensus_finality_lag_epochs` series. #163
  encodes "exactly one of `presentEpoch` or `presentEpochDivisor`" as a
  `oneOf` on the chart schema so neither an ambiguous nor an empty
  selector can render. The three other CLs keep their direct-metric
  branch unchanged.

The live recording rules produced non-empty execution and consensus head
change series, zero consensus slot lag, and a non-empty derived finality-lag
series. This is the runtime proof for the `presentEpochDivisor` path added in
#163.

Missing series degrade to empty per PRD §12.5 ("unsupported/not
collected, not zero"), not render errors.

## Non-signing runtime qualification

Observed after Flux reconciled #165 and #166:

- **Pod start**: 2/2 Ready with zero restarts; all three init containers
  completed. Prysm derived its configuration, downloaded checkpoint state at
  slot 40,000, opened REST/metrics/P2P, and connected to the local Nethermind
  Engine API.
- **Peers**: both normalized peer series stayed non-zero. Prysm reached its
  default requirement of three suitable peers without lowering
  `--min-sync-peers`; Nethermind formed double-digit execution peer counts.
- **Head movement and lag**: at `2026-08-05T09:07:28Z`, after more than 15
  uninterrupted minutes of sync, the status API reported execution head
  block 33,144, 29 execution head changes and 29 consensus head changes in
  its rolling 15-minute window, consensus slot lag 0, and consensus finality
  lag 2 epochs. The pair had 10 execution peers and 11 consensus peers; both
  containers remained Ready with zero restarts.
- **Metric names**: the live scrape contains `connected_libp2p_peers`,
  `beacon_head_slot`, `beacon_clock_time_slot`, and
  `beacon_finalized_epoch`; the corresponding normalized head-change,
  slot-lag, and derived finality-lag series are non-empty.

Test contract asserts that this release, plus the other eight Ephemery
pairs, all render with the same EKS-required patches (`valuesFiles`, dev
telemetry, `aws-engine-secrets` Engine JWT); a missing overlay patch on
any release fails CI.

## Signing qualification

**Non-signing by design.** The assignment binds a synthetic (draft,
`synthetic: true`) ValidatorIdentity via `validatorRef`, but
`signingEnabled` is `false`, so no validator client renders. The
ServiceProfile additionally sets `signingAllowed: false`, which is
enforced rather than advisory: `tests/test_client_pair_docs_present.py`
fails any profile pairing with a CL that has no validator-client chart
adapter and still authorizes signing. Prysm ships a validator client
upstream, but the chart has no Prysm VC adapter; adding signing here
would require that adapter first (analogous to #132 for Teku).

## Not covered by this qualification

- Signing is intentionally absent; the chart has no Prysm validator-client
  adapter and the ServiceProfile disallows signing.
- Spot interruption during Nethermind's first database-directory creation was
  not injected. Marker-only and marker-plus-keystore interruption states are
  executable test cases, and a retained initialized PVC restart was exercised.
- QUIC on 9001 is declared but unexercised — no pair in the fleet owns a
  public P2P NLB except the Geth+Lighthouse baseline (#152/#157), so
  this pair syncs through outbound peers only.

## References

- Prysm CL chart adapter: [#163](https://github.com/j2d3/eth-validator-platform/pull/163)
- Nethermind EL chart adapter: [#156](https://github.com/j2d3/eth-validator-platform/pull/156)
- Nethermind+Lighthouse activation (first Nethermind release):
  [#160](https://github.com/j2d3/eth-validator-platform/pull/160)
- Nethermind bootnode/writable-path corrections:
  [#161](https://github.com/j2d3/eth-validator-platform/pull/161)
- Observed Nethermind peer metric:
  [#162](https://github.com/j2d3/eth-validator-platform/pull/162)
- Nethermind+Prysm catalog activation:
  [#164](https://github.com/j2d3/eth-validator-platform/pull/164)
- Prysm shell-only runtime correction:
  [#165](https://github.com/j2d3/eth-validator-platform/pull/165)
- Nethermind retained-PVC restart correction:
  [#166](https://github.com/j2d3/eth-validator-platform/pull/166)
- Umbrella issue: [#130](https://github.com/j2d3/eth-validator-platform/issues/130)
