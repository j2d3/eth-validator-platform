# Erigon + Lighthouse

**Extends execution diversity to a third distinct implementation strategy.**
Erigon 3.x uses its own staged-sync pipeline — architecturally distinct from
both Geth's snap and Reth's staged pipeline. Adding it to the matrix answered
whether the chart's EL-adapter dispatch generalizes to a genuinely third
strategy rather than "another Geth-shaped client."

## Why this pair

- Erigon (`erigontech/erigon`) has notable operator adoption in staked-ETH
  deployments and a very different on-disk layout (`/data/chaindata/` with a
  distinct multi-database organization).
- Lighthouse is held constant from the baseline; the interesting variable is
  entirely on the EL side.
- Erigon's Ephemery integration also introduced a subtlety around embedded
  consensus-client mode (`--externalcl`) that surfaced during review — the
  same class of failure Codex caught elsewhere with the "verify against
  runtime, not documentation" pattern.

## Identifiers

- **ServiceProfile**: `applications/profiles/dedicated-erigon-lighthouse.yaml`
- **ValidatorAssignment**: `applications/validators/assignments/assignment-ephemery-162-synthetic-erigon.yaml`
- **Synthetic ValidatorIdentity** (non-signing): `validator-ephemery-162-synthetic-erigon`
- **Node pair name**: `pair-ephemery-162-synthetic-erigon`

## Topology

- `execution`: Erigon (`erigontech/erigon:v3.5.4`) — staged-sync pipeline.
- `consensus`: Lighthouse beacon node (same image as validator #1's pair).
- `validator`: none — pair is non-signing.

## Network configuration

Uses `ephemery-162` with the same digest-pinned bundle. Erigon accepts the
Geth-compatible `genesis.json` via `erigon init --datadir=/data <genesis>`.

## Storage and restart

- Execution PVC: 50 GiB encrypted `gp3`. Marker directory is
  `/data/chaindata/` (asserted by `erigonInitCommand`; Erigon writes its
  state there on first run).
- Consensus PVC: 20 GiB encrypted `gp3`.
- No validator PVC.

## Client-specific command-line adaptations

- `erigon init --datadir=/data <genesis>` — Erigon has a Geth-style init
  subcommand (unlike Reth's `reth init --chain` or Besu's "no init
  subcommand at all").
- `erigon --datadir=/data --networkid=<id> --bootnodes=<enodes> --http
  --http.addr=0.0.0.0 --http.api=eth,net,web3 --authrpc.addr=0.0.0.0
  --authrpc.port=8551 --authrpc.vhosts='*' --authrpc.jwtsecret=/jwt/jwt.hex
  --metrics --metrics.addr=0.0.0.0 --metrics.port=6060`
- Metrics port 6060 (same as Geth/Reth) so the chart's PodMonitor scrape
  contract stays identical.

## Metric normalization

- `chain_head_block` → normalized `headBlock`.
- `p2p_peers` → normalized `peers`.
- **`headHeader` omitted** for the same PRD §12.5 reason as Reth: Erigon
  doesn't publish a separately-observed downloaded-header-frontier gauge
  distinct from the executed head.

Names are best-effort per Erigon 3.x documented series; the runtime-verify
loop applies (recording rules degrade to empty series for unknown names, no
render error).

## Non-signing qualification

Ran as `signingEnabled: false` on EKS. Sync qualification exercises the
staged-sync pipeline against Ephemery — a genuinely different sync-strategy
comparison against the other two ELs (Geth pinned to full-sync on this EKS
profile per `values-eks-ephemery.yaml`; Reth's own staged pipeline).

## Signing qualification

**Non-signing by design** and by chart contract (the assignment carries
`signingEnabled: false` and both safety flags at `false`; the synthetic
identity is refused by the projection tool's signing gate).

## Problems encountered and corrections

- **Overlay-comment wording** (Codex review on [#134](https://github.com/j2d3/eth-validator-platform/pull/134)):
  the initial commit said the pair contrasted with "Geth's snap" — but on
  the EKS profile Geth is pinned to full-sync (to avoid the snap-pivot
  failure surface from an earlier restart exercise). Corrected to name the
  three genuinely different sync strategies at runtime (Geth full, Reth
  staged, Erigon staged) rather than the client defaults.
- **Rebase-race pattern**: `#134` bounced through multiple `BEHIND main`
  states while adjacent signing-lane PRs (#133, #135) landed. The wrapper's
  `mergeStateStatus=CLEAN` contract requires re-approval after each rebase;
  standing rebase-then-notify practice moved things through.

## Remaining unqualified

- Signing (out of scope for this pair; it's the second of the two
  non-signing pairs held for operational contrast).
- Long-term Erigon-specific metric-name accuracy — verify against a live
  scrape and file a follow-up chart-values fix if needed (same runtime-verify
  pattern as Reth's original metric-name correction in #96).

## References

- Chart adapter PR (Erigon EL): [#131](https://github.com/j2d3/eth-validator-platform/pull/131)
- Catalog / activation PR: [#134](https://github.com/j2d3/eth-validator-platform/pull/134)
- Umbrella tracker: [#130](https://github.com/j2d3/eth-validator-platform/issues/130)
