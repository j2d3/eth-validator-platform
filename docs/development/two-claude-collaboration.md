# Two-Claude collaboration — a one-page setup guide

This is a portable version of the two-agent build model
([agentic-workflow.md](agentic-workflow.md)) adapted for **two Claude Code
instances** collaborating on any repository. The original experiment paired
Claude with OpenAI Codex; this variant replaces Codex with a second Claude,
which changes nothing structurally — the coordination primitives are all
GitHub-native — but does change what disciplines you need to install by hand
so the two Claude sessions don't drift into agreement.

## The one-sentence pitch

Two Claude instances, two GitHub personas, one shared repository — each
implements independently and reviews the other's work at exact head, and
neither can approve its own PRs, so every merged commit has been read by an
agent that did not write it.

## Why this trade-off

A single Claude session that writes and self-approves a change tends to
defend its own plausible logic; a second instance starting from just the
diff and the runtime evidence catches the mismatch. The cost is 2× compute
+ the coordination overhead of maintaining a review-required contract; the
benefit is that failure modes an isolated session rationalizes past
(fabricated metric names, silent typos that fall back to defaults, shell
built-ins that don't exist in a specific container image) get caught before
they land.

The design specifically depends on the two sessions **not sharing state**.
Two Claudes talking through a shared memory or with visibility into each
other's context tend to converge; the value comes from adversarial review
across an information gap.

## What you need to set up (one-time)

1. **Two GitHub accounts** with write access to the target repository. Give
   them recognizable names — e.g. `claude-a` / `claude-b`. Neither may push
   to `main`; both push to their own branch prefix only (see below).

2. **Two `gh` CLI credential directories.** Each terminal session exports
   `GH_CONFIG_DIR` to a directory containing that persona's auth:
   ```bash
   export GH_CONFIG_DIR=$HOME/.config/gh-claude-a   # or -claude-b
   ```

3. **Two separate clones** of the repository, one per persona, on disk. Do
   not point both Claude sessions at the same working tree — the sessions
   need independent commit staging and independent uncommitted-file
   footprints.

4. **Branch-prefix convention**, enforced by branch protection:
   - `claude-a/*` — only claude-a pushes here
   - `claude-b/*` — only claude-b pushes here
   - `main` — merges only through the guarded wrapper below

5. **Branch protection on `main`** with:
   - Required checks (four is a reasonable minimum: your test suite,
     terraform/manifest validation, container-runtime contracts, and any
     portal/docs contract you rely on).
   - **Require pull-request review before merging** — this is the
     load-bearing rule; it enforces the "no self-approval" contract via
     GitHub itself rather than trust.
   - **Require the review to be from a user other than the author** (GitHub
     enforces this by default when review is required; verify).

6. **Guarded merge wrapper** (`hack/merge-pr.sh` or equivalent). The
   wrapper must refuse to merge unless:
   - `mergeStateStatus == CLEAN` (branch current with main, no conflicts).
   - All required checks are `success` (not `pending`, not `neutral`).
   - A review is present **at the exact current HEAD** (rebased-away
     approvals do not count; a force-push after approval requires
     re-review).
   - The merge author identity matches the branch prefix (`claude-a/*`
     merges as claude-a, `claude-b/*` merges as claude-b).

7. **A DM channel** for out-of-band signaling. In the original experiment
   this is a pair of ignored files on a shared filesystem:
   ```
   /shared/.from_claude_a    # claude-a writes, claude-b reads
   /shared/.from_claude_b    # claude-b writes, claude-a reads
   ```
   These are file-based rather than in-repo because they carry rough drafts,
   coordination text, and evidence-in-progress that shouldn't be permanent
   history. If you don't have a shared filesystem, an in-repo folder
   (`.coord/`) added to `.gitignore` works too, provided both sessions read
   the same clone-local path — but this loses the cross-session freshness
   signal that makes the file-mtime approach useful.

## How work flows day-to-day

```
issue picked up (comment claim on GitHub)
  → agent A branches claude-a/<slug>
  → agent A implements, tests, pushes
  → agent A opens PR, DMs agent B the exact head SHA
  → agent B reviews at exact head (approve OR request changes with body)
  → CI checks land green
  → agent A runs hack/merge-pr.sh <n>
    (wrapper enforces CLEAN + green + at-head-review + author-identity)
  → merge lands on main
  → runtime signal or CI on main is observed
  → next iteration; each agent sweeps for its own review-requested PRs
    every N minutes via /loop
```

The `/loop` skill inside Claude Code is what keeps the agents moving
autonomously between human check-ins. Each session's `/loop` runs a sweep
(new review requests, new DMs, own-PR CI state, own-issue claim state),
acts on anything that changed, and reschedules itself.

## Six principles that make it work

1. **Evidence-only discipline.** Every runtime claim in a doc or PR body
   distinguishes what was actually observed at a specific timestamp from
   what remains unqualified. "Missing means unknown, never healthy-zero."

2. **Runtime-verify against production, not synthetic tests.** Test
   fixtures that shrink parameters or run in your host shell can hide
   failures that only surface at real values, in the real container image.
   Before approving a shell-fragment PR, ask whether the shell built-ins
   used exist in the target image; before approving a scrypt/subprocess
   wrapper, mentally instantiate it with production-size inputs.

3. **Quote the other agent verbatim; don't round.** When acting on a
   report from the other agent (an approval, a rejection, a runtime
   observation), the received text is the source of truth. Paraphrasing
   introduces drift the reviewer can't catch.

4. **Check for parallel-agent PRs before starting a claimed issue.**
   `gh pr list --author <you> --state open` before opening a branch on any
   claimed issue — the other agent's session may have started the same
   work from an isolated clone. Coordinating at PR time is much cheaper
   than force-pushing over a competing draft.

5. **Never self-approve; never merge without exact-head review.** The
   guarded wrapper enforces this, but write the discipline into the
   session prompt so the agent doesn't try. Self-approval is the point
   the model degrades.

6. **File follow-ups; don't gate merges on polish.** "Perfect is the
   enemy of good." Merge on real blockers; open a follow-up issue for
   cosmetic corrections. Don't invalidate an at-head approval with a
   force-push for a comment tweak — file the tweak as a follow-up.

## What NOT to do

- **Don't share memory or context between the two Claude sessions.** The
  adversarial-review value collapses if both instances have the same prior
  reasoning available. Independent sessions with independent memory files
  is the whole point.
- **Don't let one agent merge unreviewed on the "small change" argument.**
  Every merged commit was read by the other agent. If the change is truly
  trivial, review is trivial too; don't skip it.
- **Don't skip test files that guard cross-cutting contracts** because
  they're slow. If a test enforces an overlay-patch contract or an EKS
  sync-list contract, running the fast subset locally and pushing on green
  will get the slow test to catch the miss in CI — but only after the CI
  round-trip, which is a much slower feedback loop than just running the
  full suite.
- **Don't use `gh pr edit --body` for large PR bodies.** It silently
  no-ops when the GraphQL response has warnings. Use
  `gh api repos/OWNER/REPO/pulls/N -X PATCH -F body=@file.md` and verify
  with `gh api ... --jq .body | grep <marker>`.
- **Don't trust that both agents will independently discover the same
  issue.** If one agent finds a runtime defect, DM it to the other; two
  Claudes with the same context still miss the same failure modes.

## When one agent is offline

The model degrades gracefully to single-agent operation for
non-safety-critical work (docs, contained refactors) — the offline agent's
PRs just wait for review. For safety-critical work (schema changes, key
material, signing paths), pause the lane until both agents are available.
The "no self-approval" contract is worth more than throughput.

## References

- The original two-agent narrative: [agentic-workflow.md](agentic-workflow.md).
- The prompt to bootstrap a fresh Claude session into this workflow:
  [two-claude-agent-prompt.md](two-claude-agent-prompt.md).
