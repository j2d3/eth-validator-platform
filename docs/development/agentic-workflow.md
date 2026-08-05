# The two-agent build model

A narrative companion to [COLLABORATION.md](../../COLLABORATION.md). That file
is the normative rulebook; this one explains the experiment, its evolution,
and what has been learned so far.

## The premise

The repository is built by one accountable human operator (`j2d3`) working
with two independent AI coding agents:

- **Claude Code** (`5u6r054` GitHub identity) — one builder/reviewer lane.
- **OpenAI Codex** (`j2d3` GitHub identity, temporarily) — the other lane.

Each agent runs in its own interactive session with its own working directory
clone and its own `gh` credentials. Neither agent can approve its own PRs.
Every commit going to `main` has been reviewed at its exact head by the
*other* agent before the merge wrapper (`hack/merge-pr.sh`) will proceed.

## Why two agents

The premise is that adversarial review catches what single-agent reasoning
rationalizes away. An agent generating a change tends to defend its own
plausible logic; a second agent starting from the diff and the runtime
evidence is more likely to notice the mismatch.

Concrete failure modes the model has actually caught, not just claimed to:

- **Two-NLB / mixed-protocol P2P topology** (Codex rejecting Claude's initial
  design in issue #82) — two independent TCP/UDP NLBs cannot advertise one
  correctly-formed P2P endpoint; the fix required an AWS Load Balancer
  Controller mixed-protocol NLB.
- **"Sync confirmed" overclaim** — Claude paraphrased Codex's
  "containers-Ready" report into "sustained sync passed." Codex called it out
  verbatim, and the rule "quote other agents verbatim, don't round" is now
  part of Claude's operating rulebook.
- **Teku metric-name correction** — Claude's initial Teku metric map used
  `beacon_peer_count` (Prysm's shape). Codex verified against Teku's actual
  `/metrics` output and blocked the merge until `libp2p_peers` was used.
- **`validator_enabled_count` fabrication** — the initial
  `validator_platform_validator_enabled` recording rule queried a metric
  that did not exist anywhere; Codex caught it via a live Prometheus check
  and corrected the union to Lighthouse VC's actual
  `vc_validators_enabled_count`.
- **Erigon overlay wording nit** — Claude's overlay comment said "Geth's
  snap" while the EKS profile deliberately pins Geth to full-sync; Codex
  flagged the wording and Claude corrected it before the wrapper would
  merge.

None of these failures would have been detected by a single-agent workflow
short of a live production incident.

## The current cadence

```text
Pair or feature selected
  → agent A (builder) implements
  → agent B cross-reviews at the exact head
  → all four GitHub checks pass
  → agent A runs hack/merge-pr.sh <n>
    (wrapper refuses unless mergeStateStatus=CLEAN, all checks green,
     paired review present at the exact head, no remapped/stale approval)
  → merge lands on main
  → Flux reconciles (typical latency: minutes)
  → runtime evidence observed
  → next iteration
```

Two specialization refinements emerged during the initial signing bring-up:

1. **Builder-vs-promotion specialization.** Claude builds new non-signing
   client pairs (chart adapters + catalog activation). Codex promotes
   qualified pairs into signing (Terraform key container + AWS onboarding
   + Web3Signer projection + activation-flip PR). This split emerged
   organically because Codex has the sanctioned lane for live-cluster and
   trusted-local Terraform operations; Claude has the lane for chart and
   catalog work.
2. **The human runs the identity + deposit ceremony.** No agent touches
   validator keystore material or deposit transactions. The human generates
   each key offline with `EthStaker deposit CLI`, submits the 32 tETH
   deposit, and onboards the encrypted keystore via `hack/onboard-web3signer-
   keystore.py` (which the agents wrote but the human alone runs).

## The tooling that makes it safe

- **Isolated GitHub identities and clones**: each agent commits from a
  distinct working directory with distinct `gh` credentials. A commit
  authored by `5u6r054` is Claude's; a commit authored by `j2d3` is either
  Codex or the human (this ambiguity is the one known audit-trail
  weakness, tracked as a follow-up).
- **The guarded merge wrapper (`hack/merge-pr.sh`)** enforces:
  - `mergeStateStatus == CLEAN` (branch current with main, no conflicts,
    no failing required checks).
  - Reviews present at the exact current head (rebased-away approvals do
    not count, per the stale-approval defense the agents wrote themselves
    in PR #67).
  - Merge as a squash under the author's own identity email — no
    author-picker games.
- **Runtime evidence as a first-class artifact.** Immutable pages under
  [`docs/evidence/`](../evidence/) record what was actually observed on the
  live cluster at a specific commit. "The manifest looked right" is never
  accepted as evidence.
- **Exact-head language everywhere.** Approvals cite the exact 40-char
  SHA. Comments reference exact heads. The wrapper matches on exact heads.
  Language discipline prevents the "I reviewed something like this earlier"
  bug class.

## Coordination friction that remains

- **DM caches (`.from_j2d3` / `.from_5u6r054`)** are local files each agent
  writes to hand off narrative context to the other. GitHub PRs and issue
  #6 remain authoritative; the DMs are just a lower-latency handoff channel.
- **Rebase-race hell.** When both agents are shipping fast, one agent's
  approval can arrive after main has advanced and left the reviewed head
  BEHIND — requiring a rebase, which stales the approval, which requires
  another approval, which may itself be BEHIND. Handled by "rebase and
  ping" as a standing move; not eliminated.
- **Copy-and-paste through the human.** Some coordination still routes
  through the human relaying "Codex says X" to Claude in the interactive
  session. Being consciously reduced by both agents preferring GitHub
  comments over out-of-band prose.

## What "done" means for a pair

- Its chart adapter renders correctly with dedicated contract tests.
- Its catalog entries (ServiceProfile, ValidatorIdentity, ValidatorAssignment)
  exist and project into the local overlay.
- The EKS overlay patches (`valuesFiles`, dev telemetry, Engine JWT) are
  applied to its release, enforced by a per-release CI assertion.
- Non-signing sync is qualified against the live network.
- Its `docs/client-pairs/<execution>-<consensus>.md` profile page exists.
- If it's a signing pair: the disjoint validator identity is deposited,
  onboarded, projected, activated, and observed publishing at least one
  attributable attestation, with the evidence recorded under
  [`docs/evidence/`](../evidence/).

## What "done" means for the workflow

Explicitly not proven yet:

- **Long-term stability of the cadence.** Every hour of shipping generates
  ~5 PRs across both agents; the collaboration model has never run for a
  week at that pace.
- **Recovery from an agent going rogue.** The wrapper refuses malformed
  merges, but there is no drill for a hostile agent PR. Human review is
  still the final safety net.
- **Scaling beyond two lanes.** Adding a third agent would multiply
  coordination combinatorics; the current DM + issue-6 channel would
  probably not scale as-is.

## Metrics worth measuring

Not yet instrumented, but the honest measurable signals would be:

- **Cycle time**: pair-selected → first-attestation.
- **Rework rate**: how many PRs required a rebase or a corrections commit
  after review.
- **Review catches**: PRs where the other agent blocked or corrected a
  substantive defect.
- **Human intervention points**: how many places the workflow required
  human judgment vs mechanical agent progress.

## What the model preserves

The two agents do not remove the operator; they move the operator upward.
The human no longer types every manifest, but still owns:

- What enters the system (product scope, PRD, ADRs).
- Every irreversible action (key generation, deposit, trusted-local Terraform
  apply, credential rotation).
- Every action against real money (deposits, mainnet — which this platform
  deliberately does not touch).
- The final safety net when both agents are wrong.

The experiment is whether that arrangement produces higher-quality software
faster than a single-agent or single-human lane, at the cost of coordination
overhead worth paying.
