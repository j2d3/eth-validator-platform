# Two-Claude collaboration — a one-page setup guide

This is a portable version of the two-agent build model
([agentic-workflow.md](agentic-workflow.md)) adapted for **two Claude Code
instances** collaborating on any repository. The original experiment paired
Claude with OpenAI Codex; this variant replaces Codex with a second Claude,
while retaining GitHub as the shared coordination and review surface.

## Summary

Two Claude instances, two GitHub personas, one shared repository — each
implements independently and reviews the other's work at exact head, and
neither can approve its own PRs, so ordinary agent-authored changes are read
by an agent session that did not write them.

## Why this trade-off

A second session that starts from the exact diff and evidence can notice an
assumption carried by the authoring session. The costs are additional compute
and coordination. In this experiment, independent review caught fabricated
metric names, silent configuration fallback, and utilities absent from the
target container before those changes landed.

Independent review does not require artificial secrecy. Let the reviewer make
a diff-first pass before reading the author's reasoning, then use the PR to
exchange context and resolve disagreements.

## What you need to set up (one-time)

1. **Two GitHub accounts** with write access to the target repository. Give
   them recognizable names — e.g. `claude-a` / `claude-b`. Neither may push
   to `main`; both push to their own branch prefix only (see below).

2. **Two `gh` CLI credential directories.** Each terminal session exports
   `GH_CONFIG_DIR` to a directory containing that persona's auth:
   ```bash
   export GH_CONFIG_DIR=$HOME/.config/gh-claude-a   # or -claude-b
   ```
   On macOS, separate config directories do not isolate Keychain-backed
   tokens. Use protected file-backed `gh` storage as described in
   [two-agent-setup.md](two-agent-setup.md), or use separate OS identities.

3. **Two separate clones** of the repository, one per persona, on disk. Do
   not point both Claude sessions at the same working tree — the sessions
   need independent commit staging and independent uncommitted-file
   footprints. Pin each clone to a repo-local noreply commit identity and a
   separate SSH key/host alias.

4. **Branch-prefix convention**, enforced by the merge wrapper or a separate
   repository rule if available:
   - `claude-a/*` — only claude-a pushes here
   - `claude-b/*` — only claude-b pushes here
   - `main` — merges only through the guarded wrapper below

5. **Branch protection on `main`** with:
   - The repository's actual required checks.
   - **Require pull-request review before merging** — this is the
     base review rule. The wrapper below additionally requires the exact
     paired persona, not merely any approver.

6. **Guarded merge wrapper** (`hack/merge-pr.sh` or equivalent). The
   wrapper must refuse to merge unless:
   - `mergeStateStatus == CLEAN` (branch current with main, no conflicts).
   - All required checks have completed with a repository-accepted
     conclusion; none is pending or failing.
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
   history. Both sessions must read and write the same absolute shared path.
   A gitignored file in two separate clones is two different files. Without a
   shared filesystem, use the GitHub issue and PR threads only.

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

While the Claude Code session remains available, `/loop` can repeat a sweep of
new review requests, DMs, own-PR CI state, and issue claims. It is
session-bound polling, not a durable background service. Continued operation
after a terminal or session stops requires an external scheduler or
supervisor.

## Six principles that make it work

1. **Evidence-only discipline.** Every runtime claim in a doc or PR body
   distinguishes what was actually observed at a specific timestamp from
   what remains unqualified. "Missing means unknown, never healthy-zero."

2. **Runtime-verify against the target environment, not only synthetic
   tests.** Fixtures that shrink parameters or run in your host shell can hide
   failures that only surface at real values, in the real container image.
   Before approving a shell-fragment PR, ask whether the shell built-ins
   used exist in the target image; before approving a scrypt/subprocess
   wrapper, exercise it with the real parameter shape and bounds.

3. **Preserve the other agent's exact report.** Quote an approval, rejection,
   or runtime observation before acting on it, then verify it against the
   named evidence. Paraphrasing can silently strengthen the claim.

4. **Claim before branching.** Use an atomic GitHub issue claim with a lease,
   then check all open PRs and remote branches for the same scope. Duplicate
   implementation PRs are a coordination failure, not an expected race.

5. **Never self-approve; never merge without exact-head review.** The
   guarded wrapper enforces this, but write the discipline into the
   session prompt as well.

6. **File follow-ups; don't gate merges on polish.** Merge on correctness,
   safety, and the agreed contract; open a follow-up issue for
   cosmetic corrections. Don't invalidate an at-head approval with a
   force-push for a comment tweak — file the tweak as a follow-up.

## Failure-prevention rules

- **Don't preload the reviewer with the author's conclusion.** Start from the
  diff and evidence. Share context afterward when it helps evaluate or amend
  the change.
- **Don't let one agent merge unreviewed on the "small change" argument.**
  The ordinary agent-authored path requires paired review even when that
  review is brief.
- **Don't skip tests that guard a changed cross-cutting contract.** Run the
  proportionate suite locally and let required CI enforce the complete
  repository policy.
- **Don't assume a GitHub write succeeded.** Read the PR, review, issue, or
  branch back through the API and verify the intended state.
- **Don't trust that both agents will independently discover the same
  issue.** If one agent finds a runtime defect, record it in the PR or issue;
  no reviewer is guaranteed to rediscover it unaided.

## When one agent is offline

The available agent may continue authoring bounded work, but its PRs wait for
the paired review. Do not turn an offline reviewer into an exception to the
merge contract, especially for schema, key, signing, infrastructure, or
credential paths.

## References

- The original two-agent narrative: [agentic-workflow.md](agentic-workflow.md).
- The prompt to bootstrap a fresh Claude session into this workflow:
  [two-claude-agent-prompt.md](two-claude-agent-prompt.md).
