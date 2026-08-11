# Evidence: full cold-storage restore drill, 2026-08-09/10

## What this records

The platform's first complete round trip through its recovery thesis:
cold storage → guarded restore → qualified signing → full nine-pair client
matrix → four live signing validators — every state change through paired
exact-head review, every signing enablement behind its recorded human gate.
This document is the drill's evidence record and its lessons register; the
procedures it exercised live in `docs/runbooks/eks-cold-standby.md`,
`docs/runbooks/eks-signing-recovery.md`, and
`docs/runbooks/signing-restore-after-cold-standby.md`.

## Measured timeline (UTC)

Externally observed lifecycle transitions, captured by an independent
30-second poller against the public `ops.g.j2d3.com/api/status` endpoint —
deliberately outside the cluster, so it measures what an operator (or
customer) would actually see:

| Time | Observation |
|---|---|
| 2026-08-09 ~18:45 | Restore begins from encrypted final snapshot `…cold-final-20260808-221449` |
| 2026-08-09 18:51:11 | Baseline confirmed: COLD — ops endpoint does not connect |
| 2026-08-10 00:06:08 | First transition: COLD → RUNNING (3 nodes, 1 Ethereum pod) |
| 2026-08-10 00:30 | Validator 01 signing again: doppelganger cleared, 3 signings permitted, 0 prevented |
| 2026-08-10 03:03–03:06 | Transient 3-minute COLD blip (single failed poll; self-healed; poller debounce added) |
| 2026-08-10 15:37 | Spot capacity 2→9 nodes executed; nine-pair matrix reconciling |
| 2026-08-10 ~20:10 | All four deposited validators enabled across four distinct client pairs |

The ~5h15m from restore start to first external RUNNING observation was
dominated by one now-eliminated blocker (stale operations DNS, below) and
Flux/ingress sequencing — EKS and RDS themselves restored in under 25
minutes, consistent with the first drill's measurements.

## What this drill fixed permanently

1. **Stale operations DNS after restore** (#226). A restore creates a new
   NLB; the Terraform-owned `ops.g.j2d3.com` CNAME kept pointing at the
   deleted one, which is why the platform was externally COLD for hours while
   internally healthy. `hack/eks-cold-standby.sh refresh-dns` now discovers
   and validates the live hostname and applies a plan guarded to exactly one
   Route 53 record. Next restore runs it as a listed step.
2. **BEHIND-churn on the PR queue** (#219/#220). Every merge invalidated all
   other open PRs' branches and approvals. The auto-rebase workflow now
   advances the lowest-numbered BEHIND PR per push to `main` — one CI matrix
   at a time. (Known residual: GitHub computes `mergeStateStatus` lazily, so
   the run immediately after a merge can see no candidate; a bounded retry is
   the identified fix.)
3. **The one-gate signing restore is real** (#222/#223, exercised by
   #225/#233/#235/#236). Automated read-only qualification, one recorded
   human GO per activation, enablement through ordinary reviewed Git merges.
   Four validators returned to duty this way; the gate record cadence (GO
   posted before the PR, comment ID cited in the manifest) settled into
   routine by the third activation.

## What this drill surfaced — the lessons register

1. **Client pins expire against a moving network while the platform sleeps.**
   The sharpest lesson. Reth v1.6.0 was correct when pinned; during and after
   cold storage, Ephemery-162 activated its BPO timestamp forks, and on
   restore Reth advertised a genesis-only fork ID that every peer rejected
   post-RLPx (`fork id mismatch`, #237). Geth's pin happened to be newer.
   Nothing in preflight caught it, because the images were byte-identical to
   what had worked before — the *network* had moved, not the repo.
   **Implication for next restore:** a pre-restore (or post-restore,
   pre-qualification) check that each pinned execution image's fork schedule
   covers the network's current fork state. Until that exists, treat "pair
   syncs zero peers with clean RLPx" as this signature.
2. **Identity migration is a first-class recovery strategy.** When the Reth
   pairs could not qualify, validators 02 and 04 were not held hostage: their
   identities moved to healthy pairs (#235, #236) under §5.1/§5.3, with the
   vacated assignments rebound to draft synthetic identities. Client
   diversity is not just risk spreading — it is spare qualified capacity for
   exactly this moment.
3. **Diagnose with a gated filter, not conjecture.** The Reth root cause went
   hypothesis → falsification (genesis hashes matched) → overlay-scoped debug
   filter (#232, corrected target `net=debug` in #234) → named error → pinned
   fix (#237), in about two hours. The pattern is reusable: base chart
   untouched, overlay-only verbosity, explicit removal note, disconnect
   reason chooses the fix.
4. **State-pinning contracts must move with the state — and that is a
   feature.** Every resume/activation PR that forgot a by-name pin went red
   until the contracts moved in the same diff. The friction is the design:
   fleet state changes are enumerated, reviewed, and self-documenting. The
   checklist (which pins move for which change class) is now in the issue #6
   record and saved future PRs from red-CI round-trips by their second
   iteration.
5. **Review freshness discipline held under pressure.** GitHub remapped stale
   approvals onto new heads repeatedly; the wrapper's submitted-at check
   caught every instance. Two in-flight-head races (approval attaching to a
   just-amended head) were caught by post-hoc diff and disclosed; sequencing
   the citation amend before requesting review eliminates the race class.
6. **The teardown fingerprint remains unimplemented.** The signing-restore
   qualification contract (#222) defines pre-teardown fingerprint capture;
   `down` still does not perform it. This teardown must capture the
   fingerprint manually (schema version, per-validator maxima, record counts,
   content digest) and record it beside the snapshot ID — or land the
   implementation first. A cold state without a fingerprint routes the next
   signing restore through the manual RDS drill by design.

## External transition log (verbatim)

```
2026-08-09T18:51:11Z portal-state: COLD (was: <start>)
2026-08-10T00:06:08Z portal-state: RUNNING nodes=3 pods=1 (was: COLD)
2026-08-10T03:03:36Z portal-state: COLD (was: RUNNING nodes=3 pods=1)   # single-poll blip; debounce added
2026-08-10T03:06:41Z portal-state: RUNNING nodes=3 pods=2 (was: COLD)
```

The 03:03 entry is itself a lesson in miniature: a single-sample classifier
flipped state on one failed poll — the same twitchiness the portal's
lifecycle banner was reviewed for. The poller now requires two consecutive
agreeing samples, mirroring the fix philosophy applied in #221's review.

## Signing evidence pointers

- Validator 01 restore: issue #6, 2026-08-10T00:30:59Z (doppelganger
  cleared; `slashingPermittedTotal=3`, `slashingPreventedTotal=0`).
- Gate records: #233 (validator 03), #235 (validator 02, pair migration),
  #236 (validator 04, pair migration) — each scoped, each preceding merge.
