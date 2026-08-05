# Nethermind + Lighthouse

**First rendered release exercising the Nethermind EL adapter.** Chart
adapter landed in #156 with a runtime-verified command shape
(`--config=none --Init.ChainSpecPath=/network/files/chainspec.json
--KeyStore.KeyStoreDirectory=/data/keystore`) against Codex's live
1.39.2 probe. This catalog PR turns that adapter into a live workload
target for the first time, paired with the well-exercised Lighthouse CL
so the observed variable is the .NET-based EL alone.

## Why this pair

- **Fifth distinct EL runtime.** The four earlier ELs are Go (Geth,
  Erigon) and Rust (Reth) and Java (Besu). Nethermind is .NET/C#. The
  runtime characteristic worth watching is CLR memory behavior under
  the chart's UID-1000 read-only-root Pod contract.
- **Nethermind-format chainspec, not geth-format genesis.json.** This
  pair is the first that consumes `chainspec.json` from the Ephemery
  bundle (the file was there all along per Codex's discovery on
  issue #6; #156 added it to the artifactBundle schema mapping).
- **Well-exercised CL.** Lighthouse has been in the fleet since the
  baseline vertical slice. Pairing Nethermind with Lighthouse isolates
  the EL variable — anything anomalous is Nethermind-specific.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-nethermind-lighthouse.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-nethermind-lighthouse.yaml`
- **Synthetic ValidatorIdentity** (non-signing): `validator-ephemery-162-synthetic-nethermind-lighthouse`
- **Node pair name**: `pair-ephemery-162-synthetic-nethermind-lighthouse`

## Topology

- `execution`: Nethermind 1.39.2 (image + digest from #156's
  values.yaml, index-pinned).
- `consensus`: Lighthouse beacon node (same image as
  [`geth-lighthouse`](geth-lighthouse.md)).
- `validator`: none — pair is non-signing by design per issue #130's
  non-goals.

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle. Nethermind
consumes `chainspec.json` from the bundle via `--Init.ChainSpecPath`;
Lighthouse consumes the same `config.yaml` + `boot_enr.txt` +
`genesis.ssz` other CL pairs use. The `executionChainspec: chainspec.json`
mapping was added to the network profile in #156.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3` (its own; separate from other
  pairs' PVCs because `nodePairRef` differs).
- Consensus PVC: 20 GiB encrypted `gp3`.
- No validator PVC (chart renders it only when `validator.enabled=true`,
  which stays `false` on this non-signing pair).
- Restart-safe init: #156's Nethermind init helper implements a
  state-machine that writes the platform identity marker before any
  mkdirs and treats a marker-only / marker+keystore-only claim as a
  resumable first start (foreign-content claims fail closed).
  Executable rendered-shell test covers empty / interrupted-before-
  keystore / interrupted-before-db / initialized / wrong-network /
  unrelated-data / foreign-unmarked states.

## Engine API wiring

Nethermind's `--JsonRpc.EngineHost + --JsonRpc.EnginePort=8551` speaks
the standard Engine API. Lighthouse's `--execution-endpoint=http://
127.0.0.1:8551` addresses the localhost side of the paired Pod. The
Engine JWT (`/jwt/jwt.hex`) is shared by both containers via the same
projected volume, sourced from Secrets Manager via the AWS SecretStore
per the EKS overlay (per-pair scoped by `fullnameOverride`).

## Nethermind-specific command-line adaptations

Per #156's chart adapter:

- `--config=none` — disables the built-in network selector so all
  runtime state is driven by `--Init.ChainSpecPath` and explicit CLI
  flags.
- `--Init.ChainSpecPath=/network/files/chainspec.json` — the
  Ephemery-format chainspec (JSON with `name/engine/params/genesis/
  accounts/nodes` shape per `ethpandaops/ethereum-genesis-generator`'s
  `tpl-chainspec.json`).
- `--Init.BaseDbPath=/data` — data directory root; Nethermind creates
  `/data/nethermind_db` on first execution start.
- `--Init.StaticNodesPath=/tmp/static-nodes.json` and
  `--Init.TrustedNodesPath=/tmp/trusted-nodes.json` — keep reconstructible peer
  files on the writable ephemeral mount instead of Nethermind's read-only
  application directory or the durable execution PVC. Logs use `/tmp/logs`.
- `--KeyStore.KeyStoreDirectory=/data/keystore` — required override
  because Nethermind's default `/nethermind/keystore/node.key.plain`
  path fails under the chart's read-only-root Pod contract. The init
  helper creates this directory (idempotently, before any Nethermind
  process starts).
- `--Network.Bootnodes` receives the complete line-delimited `enodes.txt`
  bundle file as one comma-delimited argument.

The first EKS start verified the init state machine but exposed two run-command
requirements before Engine API opened: passing newline-delimited enodes as one
argument made Nethermind parse the second line as an invalid ENR, and the
default static-nodes path targeted read-only `/nethermind`. The corrected
command was then exercised against the pinned image with UID 1000, a read-only
root filesystem, dropped capabilities, and no-new-privileges; it parsed both
enodes, opened JSON-RPC and Engine API, and formed peers.

## Metric normalization

**Status: `nethermind_blocks` and `ethereum_peer_count` are observed on the
active Ephemery Pod running the pinned 1.39.2 image.** `nethermind_peers` is
absent. `nethermind_sync_peers` is present but partitioned by remote-client
type, so the chart uses the aggregate `ethereum_peer_count` gauge. The
`executionMetricsPath` chart dispatcher (from #158) sends Prometheus
to `/metrics` (same as Besu, different from Geth/Reth/Erigon's
`/debug/metrics/prometheus`).

The recording rules build `validator_platform_execution_*` unions
across all declared adapters, filtered by `execution_client="nethermind"`
label. `headHeader` is deliberately omitted (per PRD §12.5 —
Nethermind does not publish a separately-observed downloaded-header
frontier gauge distinct from executed head).

## Non-signing qualification gates

Per Codex's ask on issue #6: the runtime qualification for this pair
after Flux reconciles it should record observable evidence for each of:

- **Pod start**: both containers become Ready (2/2), Nethermind
  container exits its init-verify state cleanly, no restart-loop.
- **Peers**: `validator_platform_execution_peers{execution_client=
  "nethermind"}` reports a non-zero value; Lighthouse
  `sync_peers_per_status` also reports non-zero.
- **Head movement**: `validator_platform_execution_head_block{
  execution_client="nethermind"}` advances over a 15-minute window
  (aggregated as the existing `changes()` rule).
- **Actual metric names present**: the live scrape exposes
  `nethermind_blocks` and `ethereum_peer_count`; it does not expose the
  originally configured `nethermind_peers` name. The chart contract uses
  only the observed aggregate names.

Test contract asserts that this release, plus the other seven Ephemery
pairs, all render with the same EKS-required patches (`valuesFiles`,
dev telemetry, `aws-engine-secrets` Engine JWT).

## Signing qualification

**Non-signing by design.** The assignment binds a synthetic
(draft, `synthetic: true`) ValidatorIdentity via `validatorRef`, but
`signingEnabled` is `false`, so no validator client renders. Signing on
a Nethermind pair would use the standard
Lighthouse VC pattern already exercised for validators #1 and #2.

## References

- Nethermind EL chart adapter: [#156](https://github.com/j2d3/eth-validator-platform/pull/156)
- Besu-metrics-path adapter (same `/metrics` path used by Nethermind):
  [#158](https://github.com/j2d3/eth-validator-platform/pull/158)
- Codex's chainspec discovery on issue #6:
  [comment 5188068106](https://github.com/j2d3/eth-validator-platform/issues/6#issuecomment-5188068106)
- Umbrella issue: [#130](https://github.com/j2d3/eth-validator-platform/issues/130)
