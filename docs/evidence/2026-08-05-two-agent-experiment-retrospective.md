# Two-agent build model — five-day operating retrospective

## Observation identity

| Field | Value |
|---|---|
| UTC window | 2026-08-01T00:00Z through 2026-08-05T16:03Z |
| Repository commit at record close | `a328365` (post-#197 merge) |
| Environment | Amazon EKS `eth-validator-platform-dev`, `us-west-2` |
| Network | Ephemery generation 162 (network profile `ephemery-162`) |
| Collaboration model | Two coding agents (Claude Code as `5u6r054`, OpenAI Codex as `j2d3`), one accountable human operator, guarded merge wrapper `hack/merge-pr.sh`, GitHub-native coordination + file-based DM caches |

This record covers **the collaboration**, not the platform it produced. The
platform evidence records under the same directory cover product outcomes
(EKS NetworkPolicy, first signing validator, Spot rebalance recovery). This
one asks a different question: what did running the two-agent build model
for five days at production cadence actually establish, and what didn't it.

No cloud account ID, ARN, network address, IP address, secret value,
private key, mnemonic, validator public key, keystore path, or database
endpoint is included.

## Aggregate activity

Merges to `main` between 2026-08-01T00:00Z and 2026-08-05T16:03Z:

| Author | Count |
|---:|---|
| `j2d3` (Codex + occasional operator) | 74 |
| `5u6r054` (Claude Code) | 23 |
| `app/github-actions` (dependency updates + auto-merges) | 3 |
| **Total merges to `main`** | **~100** |
| Total PRs (open + closed + merged) since 2026-08-01 | 149 |
| Issues closed in the window | 18 |

The `j2d3` author count is not equivalent to a single-agent Codex output:
during this window, the operator merged some `j2d3`-authored changes
without an at-head Claude review (early-window bootstrap work, Terraform
apply plans that must run under a trusted-local identity, dependency
updates from `app/github-actions`). The 23/74 ratio therefore understates
the amount of work the two-agent contract actually gated. The gated-path
subset — PRs opened under an agent branch prefix (`claude/*` or `codex/*`)
that landed on `main` through the merge wrapper — is the one where the
model's discipline was actually exercised.

**Platform outcomes across the window** (each has its own evidence record
or runbook; enumerated here so the collaboration context is complete):

- Nine active Ephemery-162 client pairs (four signing, five non-signing).
- All four consensus-client adapters (Lighthouse, Teku, Nimbus, Prysm)
  and all five execution-client adapters (Geth, Reth, Erigon, Besu,
  Nethermind) exercised in at least one live rendered pair.
- Container image supply-chain evidence (weekly Trivy matrix with
  inventory-bound aggregation, CycloneDX SBOMs per subject, and a
  public-safe portal card) — #190 → #194 → #195.
- Fleet container-resource normalization (`validator_platform_container_*`
  via cAdvisor with kube-state-metrics label join) — #168.
- Runtime alerts + PVC-exhaustion forecast recording rules — #172, #189.
- RDS slashing-recovery drill design + preflight tooling under #180
  (not executed).
- Validator-key ceremony helper for the non-automated human-boundary step
  of #146 — #184.

## What the paired-review model actually caught

Each entry names the specific PR (or referenced issue) where the second
agent blocked or corrected a change the authoring agent had produced. All
of these were caught by an agent that did not write the change, not by
CI, and would have landed on `main` under a single-agent workflow without
equivalent independent review.

### Fabricated or wrong metric names

- **`validator_enabled_count`** — Claude's initial validator-enabled
  recording rule queried a metric that did not exist on any client in the
  fleet. Codex verified against the live Lighthouse VC's `/metrics`
  output and blocked the merge until the union used
  `vc_validators_enabled_count`.
- **`beacon_peer_count` on Teku** — Claude's initial Teku metric map
  reused Prysm's shape. Codex verified against Teku's own `/metrics`
  output and required `libp2p_peers`.
- **`nethermind_peers`** (#162) — the initial Nethermind chart adapter
  declared `nethermind_peers` for the peer count. Codex's live scrape
  showed the actual Nethermind Prometheus surface publishes
  `ethereum_peer_count` and `nethermind_sync_peers` (partitioned by
  remote-client-type); `nethermind_peers` is absent. Fix landed as a
  runtime-observed correction after the pair activated.
- **`process_cpu_seconds_total` / `process_resident_memory_bytes`**
  (#168) — the original per-pair container-resource rules selected on
  client-application metrics that Geth/Reth/Nethermind images don't
  expose, producing null execution memory for six of nine pairs. Flagged
  by Claude against the public status API after #164; Codex replaced
  with cAdvisor cgroup-level metrics (`container_cpu_usage_seconds_total`
  / `container_memory_working_set_bytes`) joined through
  `kube_persistentvolumeclaim_labels`.

### Silent configuration fallback

- **`syncmode` typo → snap-sync default** (#187) — the chart's
  `executionClients.geth` schema entry had no
  `additionalProperties: false`, so a typo `syncmode: full` (lowercase,
  non-canonical) passed schema validation and got silently ignored
  because the chart reads `syncMode` camelCase. Codex added the
  `additionalProperties: false` guard + direct-render tests for both
  `--syncmode=snap` and `--syncmode=full`, closing the exact class of
  defect that would surface as the #84 Geth mid-snap-sync crash-loop.

### Container-image utility gaps

- **Prysm image ships `/bin/sh` but no `grep`/`sed`/`awk`** (#163 →
  #165) — Claude's initial Prysm chart adapter used `grep -Eq` and
  `grep -Ev` in the pre-exec Ephemery→Prysm config derivation. Rendered-
  shell tests passed on the host shell (which has grep), but the actual
  v7.1.8 image ships neither. First live Nethermind+Prysm Pod
  crash-looped. Codex rewrote the derivation with POSIX built-ins
  (`while read` + `case` + `test` + `printf`) and added a regression
  test asserting `grep`/`sed`/`awk` do not appear as commands in the
  rendered pre-exec fragment.

### Wrong bootnode / on-disk path

- **Nethermind `--Network.Bootnodes` newline parsing** (#161) — the
  Ephemery bundle's `enodes.txt` contains two newline-delimited enodes.
  The initial Nethermind chart adapter read the file with `cat` and
  passed the embedded newline as one `--Network.Bootnodes` value;
  Nethermind 1.39.2 parsed line 2 as an invalid ENR and exited with
  `ConfigurationErrorsException`. Codex switched to `tr '\n' ','`
  normalization with runtime evidence from the exact pinned image.
- **Nethermind static-nodes / trusted-nodes / log paths under
  `/nethermind`** (#161) — the same live start attempted to create
  `/nethermind/static-nodes.json` on the read-only root. Codex added
  `--Init.StaticNodesPath=/tmp/static-nodes.json`, `--Init.
  TrustedNodesPath=/tmp/trusted-nodes.json`, and
  `--Init.LogDirectory=/tmp/logs`. A negative assertion in the
  regression test prevents these paths from being redirected to `/data`
  (which would corrupt the init state-machine's foreign-data check).
- **Nethermind PVC restart-guard checked wrong path** (#166) — after
  #165 deployed, the retained PVC exposed a second bug: the init
  state-machine guard expected `/data/nethermind_db`, but the actual
  `--Init.BaseDbPath=/data` layout creates RocksDB directories
  (`blocks`, `headers`, `metadata`, `state`) directly below `/data`.
  Read-only diagnostic mount confirmed no `nethermind_db`; guard
  corrected.

### Missing cross-cutting overlay patch

- **New pair rendered against local defaults, not EKS overlay** (#164
  → `118d6e1` courtesy-fix) — Claude's initial Nethermind+Prysm catalog
  activation added the ServiceProfile + ValidatorIdentity + assignment
  + docs page, and passed all local tests. It did **not** add the
  per-pair patch block to `platform/apps/nodes/dev/kustomization.yaml`
  (valuesFiles + telemetry + `aws-engine-secrets` Engine JWT). CI's
  `test_signing_node_layer_waits_for_signer_application` caught the
  missing sorted-release-name entry, and a parallel Claude call pushed
  the corrective commit. Cause: the local test run had excluded
  `tests/test_eks_ephemery_sync_contracts.py` via `--ignore` for speed;
  that file is the exact contract that guards the overlay per-release
  patch requirement.
- **Missing `Nethermind+Lighthouse` row in `docs/client-pairs/README.md`
  table** — the same #164 review found that the framing table had been
  one pair behind the fleet since #160 landed, and the courtesy-fix
  extended the docs update.

### Stale-approval race + at-head enforcement

- **Approved head H rebased to H+1, wrapper refuses to merge** — this
  is the intended behavior of the merge wrapper, but it surfaced
  frequently enough during high-cadence periods to reshape review
  practice. When both agents ship at the ~5-PR/hour peak, one agent's
  approval regularly arrives after main has advanced and left the
  reviewed head `BEHIND`. The wrapper correctly refuses. The mitigation
  is "rebase and ping" as a standing move (paired agent re-reviews the
  new head), not a wrapper bypass.

### Language drift (paraphrase → stronger claim)

- **"Sync confirmed" overclaim** (early in the window) — Claude
  paraphrased Codex's "containers-Ready" report into "sustained sync
  passed." Codex flagged the exact paraphrase; the rule "quote the
  other agent verbatim; do not silently strengthen 'Ready' into
  'synced' or 'attempted' into 'qualified'" is now written into the
  Claude-side session prompt (see
  [`two-claude-agent-prompt.md`](../development/two-claude-agent-prompt.md)).

### Design pushback that changed direction

- **Two-NLB / mixed-protocol P2P topology** (issue #82) — Claude's
  initial P2P networking design proposed two independent TCP and UDP
  NLBs. Codex rejected it: two independent load balancers cannot
  advertise one correctly-formed P2P endpoint because Ethereum discv5
  requires the TCP and UDP sides of a peer to agree at the same
  `(host, port)` pair. The redesign landed as one mixed-protocol NLB
  through the AWS Load Balancer Controller with
  `aws-load-balancer-enable-tcp-udp-listener: "true"`, then hardened
  in #188 with render-time fail-closed guards on the three failure
  modes (legacy `aws-load-balancer-type` annotation, instance-target
  type, missing TCP-UDP-listener opt-in).

## Coordination failure modes that surfaced (and the mitigations installed)

### Parallel-agent same-issue claims

- **What happened** (twice in the window): both agents' sessions
  claimed the same issue independently, each opening a PR from an
  isolated clone. First occurrence — #164 vs. a parallel Claude
  courtesy-fix; both ended up on the same branch by accident. Second
  occurrence — #192 (mine) vs. #193 (parallel Claude) for the #43
  exact-inventory slice; Codex closed both as duplicates and
  consolidated into his own #194.
- **Mitigation installed**: `gh pr list --author <you> --state open`
  before opening a branch on any claimed issue, plus a search of the
  paired agent's `<other>/*` branch prefix. Encoded in
  [`feedback_check_parallel_claude_before_starting.md`](../../.claude/projects/-Users-johndurkin-personal-galaxy/memory/feedback_check_parallel_claude_before_starting.md)
  and in the session prompt.
- **Not solved**: the underlying claim primitive is still a natural-
  language comment on a GitHub issue. A future iteration should use an
  atomic-lease mechanism (a labeled issue with a bot-enforced lease
  expiration) so two sessions cannot both believe they hold the claim.

### `gh pr edit --body` silent no-op

- **What happened** (#163 amendment): `gh pr edit --body "$(cat <<'EOF' ... EOF)"`
  returned exit code 0 with a GraphQL projects-classic deprecation
  warning printed to the same output. The API body was **not** updated;
  the operator (Claude) reported the update as successful. Codex
  detected the stale body via a second read from a different session
  and requested the correction.
- **Mitigation installed**: use
  `gh api repos/OWNER/REPO/pulls/N -X PATCH -F body=@file.md` for large
  bodies; verify with `gh api ... --jq .body | grep <marker>` before
  reporting success. Encoded in the session prompt as "verify every
  GitHub write."

### DM-file two-clone/two-files problem

- **What happened**: The DM channel was originally described as
  `.from_<sender>` files. Each agent read the file *in its own clone*,
  but each also wrote *to its own clone's copy*. The two files were
  never the same file; each agent read stale content indefinitely.
  Fixed mid-experiment by hard-coding absolute paths — `5u6r054` writes
  to `/personal/galaxy/.from_5u6r054` (Codex's clone reads it there),
  and Codex writes to `/personal/galaxy-claude/.from_j2d3` (Claude's
  clone reads it there).
- **Mitigation installed**: absolute cross-clone paths documented in
  both agents' prompts; if a shared filesystem isn't available, use
  GitHub PR/issue threads as the coordination channel — slower but
  more durable, no aliasing risk.

### Session-bound polling ≠ autonomous progress

- **What happened**: `/loop` (Claude's session-bound polling primitive)
  keeps a Claude session moving between human check-ins, but it stops
  when the terminal closes. Both agents repeatedly reached a clean
  queue and stopped polling; the next tick required a human to
  re-invoke `/loop` (or Codex's equivalent). A later local supervisor
  loop bridged this for one review-and-merge cycle but is not a
  general durable-daemon solution.
- **Mitigation installed**: honesty framing in
  [`two-agent-setup.md`](../development/two-agent-setup.md) and in the
  agentic-workflow doc — session-bound polling is not a durable
  background service; genuine unattended operation needs an external
  scheduler with timeouts, durable queue state, and restart behavior.

### Worker doesn't exit after posting its GitHub artifact

- **What happened**: A Claude worker posted its formal PR review
  successfully but did not exit. The supervisor blocked on the
  still-running worker until the process was terminated manually.
- **Mitigation installed**: maximum-runtime timeouts on worker
  processes; verify the expected GitHub artifact (review, PR, branch)
  via a separate API call rather than trusting the worker's final
  prose.

### Sandbox vs host credential asymmetry

- **What happened**: A sandboxed Codex worker (started via
  `codex exec --ephemeral --sandbox workspace-write`) could not read
  the host's Keychain-backed `gh` token or resolve a container
  registry, even though the interactive host session could. The worker
  hit a credentials-required error path where the interactive session
  didn't.
- **Mitigation installed**: the trusted host process owns credentials
  and supplies already-verified public inputs to the worker; don't
  pass credentials in prompts or handoff files.

### Test-suite `--ignore` masks cross-cutting overlay contracts

- **What happened**: local `pytest tests/
  --ignore=tests/test_eks_ephemery_sync_contracts.py` runs skipped the
  exact test that guards the EKS overlay's per-release patch contract.
  Claude reported "308/308 pass" and pushed #164; CI failed on the
  ignored file.
- **Mitigation installed**: run the full `pytest tests/` (no
  `--ignore`) before pushing any catalog PR; encoded in the session
  prompt's Working Habits section.

## What we would do differently on a clean-slate start

1. **Atomic-lease claim primitive.** A GitHub-issue-based lease with
   a bot-enforced expiration would have prevented both of the
   parallel-agent same-issue collisions we hit. Not implemented in
   this experiment.
2. **Shared-filesystem DM path resolved at setup.** The DM channel is
   valuable, but the two-clone aliasing bug wasted several coordination
   cycles before it was caught. A setup script that writes the
   absolute cross-clone paths into each session's initial context —
   and errors out if they don't resolve to the same file — would have
   caught this immediately.
3. **`gh api ... -X PATCH -F body=@file.md` as the default** for any
   PR body over ~200 lines. Small bodies work fine with
   `gh pr edit --body`; large ones should use REST directly.
4. **External durable supervisor from day one**, not
   session-bound polling with a manual re-invocation ritual. Even the
   simplest cron loop that dispatched to fresh worker processes would
   have removed the "no PR queued → stop polling → wait for human to
   restart" idle mode we hit repeatedly late in the window.
5. **Overlay-guard tests marked as required for their scope.** The
   `tests/test_eks_ephemery_sync_contracts.py` file guards the EKS
   per-release patch contract. A local script that refuses to push
   a catalog PR unless that specific test class ran green would have
   caught #164's overlay-patch omission before the CI round-trip.
6. **Six-item comparison table for the three configurations** (see
   [`two-agent-setup.md`](../development/two-agent-setup.md)) drafted
   before starting, not derived from experience. The same-model-
   correlation risk on same-vendor pairs is the honest disclosure a
   team should evaluate before choosing a configuration.

## What this record does NOT establish

- **Long-term stability of the cadence.** Peak observed cadence was
  ~5 PRs/hour across both agents for stretches of several hours;
  sustained multi-day operation at that pace is unknown. Both agents
  repeatedly reached quiet queues and stopped polling.
- **Mainnet suitability.** Every activation in the window was
  Ephemery testnet (generation 162) with synthetic identities and
  32 tETH deposits. No real-value validator was exposed to this
  pipeline.
- **Large-team scalability.** The model was tested with two agents +
  one human. It does not establish behavior with three or more agents,
  contested reviews across many parallel PRs, or agent teams with
  differentiated authority.
- **Security posture beyond the defects observed.** The paired-review
  model caught the specific classes of defect enumerated above. It
  does not prove absence of an entire category (e.g. a supply-chain
  compromise that both agents' base training would rationalize).
- **Provider-independent review.** The Claude+Codex mix caught defects
  each individually would likely have missed; two same-vendor sessions
  share provider outages, quota limits, and model-family defaults.
  This experiment does not establish how much of the caught-defect
  count depended on the two-vendor asymmetry vs. the review discipline
  alone.
- **Hostile-agent resilience.** The merge wrapper enforces the
  paired-review contract on the honest path; there is no drill for a
  compromised agent attempting to author a malicious PR that its
  partner (also compromised, or fooled) approves.
- **Recovery from the paired reviewer being permanently offline.**
  Documented as a graceful degradation to single-agent operation for
  non-safety-critical work; not exercised at length.

## References

- Two-agent narrative + evolution: [`agentic-workflow.md`](../development/agentic-workflow.md).
- Portable setup guide (all three configurations): [`two-agent-setup.md`](../development/two-agent-setup.md).
- Claude-specific setup one-pager: [`two-claude-collaboration.md`](../development/two-claude-collaboration.md).
- Claude-specific bootstrapping prompt: [`two-claude-agent-prompt.md`](../development/two-claude-agent-prompt.md).
- Evidence-record rules: [`README.md`](README.md) in this directory.
- Guarded merge wrapper source: [`hack/merge-pr.sh`](../../hack/merge-pr.sh).
- Umbrella issue for the observability slice this window closed: [#130](https://github.com/j2d3/eth-validator-platform/issues/130).
- Umbrella issue for the image supply-chain evidence work: [#43](https://github.com/j2d3/eth-validator-platform/issues/43).
