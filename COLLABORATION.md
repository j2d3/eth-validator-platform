# Collaboration on this repository

Humans and AI agents share this repository. This document is the stable reference for how we coordinate. The live coordination channel is **pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6)** — this file describes the model; the issue is where it happens.

## Roles

| Actor | GitHub identity | Branch prefix | Merge authority |
|---|---|---|---|
| the human (owner) | `j2d3` | any | Retains override to merge, pause, or reverse any action at any time |
| Codex (OpenAI CLI) | `j2d3` (the human's session) | `codex/*` | Merges own PRs via `./hack/merge-pr.sh` after Claude approves |
| Claude Code (Anthropic CLI) | `5u6r054` (collaborator, Write) | `claude/*` | Merges own PRs via `./hack/merge-pr.sh` after Codex approves |

## Human accountability

`j2d3` is a real human operator who owns both `j2d3` and `5u6r054` accounts. All AI-agent activity under either account is performed on the human's behalf; the human accepts responsibility for actions the agents take as them. The cross-review discipline, no-self-approval rule, `hack/merge-pr.sh` wrapper, and CODEOWNERS + branch protection exist to *structure* that oversight — not to *transfer* accountability.

## Coordination channels

Each GitHub primitive has exactly one job.

| Channel | Purpose |
|---|---|
| Pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6) | Claim work, hand off, request review, flag blockers |
| Draft pull requests | Visible work in progress |
| Ready pull requests | Ready-for-review artifacts; the *other* agent reviews before author merge |
| PR comments | Handoffs, cross-review, technical discussion tied to a diff |
| ADRs (`docs/adrs/`) | Durable architecture decisions |
| PRD (`docs/prd/`) | Product/architecture baseline; changes require a PR that names what shifted |

## Rules

1. **The human retains override** on any merge, review, or repository action, at any time, without justification. The rules below are the *default* structure — the human's authority does not depend on them.
2. **No self-approval; authors merge their own PRs via `./hack/merge-pr.sh`.** GitHub blocks self-approval structurally; the wrapper additionally requires an APPROVED review from the *paired* agent (`j2d3`-authored → requires `5u6r054`; `5u6r054`-authored → requires `j2d3`) on the exact current head commit, plus explicit CI-green verification.
3. **Every PR names the PRD section, safety invariant, phase-exit criterion, or operational hygiene rule it satisfies.** If none applies, that itself is a question to raise on issue #6 before proceeding.
4. **Disagreements between agents surface to the human** rather than being resolved privately. The disagreement is the signal that makes two AIs valuable. See "Disagreement template" below.
5. **Meta-tooling changes** (CI, `.github/`, hooks, Terraform apply, secret handling, cluster-foundation manifests under `clusters/`) require **explicit notice to the human on issue #6 before opening the PR**. The normal wrapper-mediated author-merges-own flow otherwise applies; the human retains override for any PR.
6. **Fail-closed for signing.** Nothing in local scripts, CI, or GitHub automation may weaken the safety invariants in PRD §5.
7. **No secrets** — no credentials, keys, mnemonic material, keystore passwords, customer data, or secret values in issue #6, PR bodies, comments, commit messages, or logs.

## Start-of-work checklist

Run these before editing files, opening a PR, or resuming work after a break:

```bash
git fetch origin --prune
gh issue list --state open
gh pr list --state open
gh pr status
gh api user --jq .login              # verify active gh identity matches your agent
git config --local user.email        # verify repo-local commit identity is noreply
```

Then read pinned issue #6 for active claims and inspect open PRs for overlapping paths. Post a claim comment on issue #6 using the template there **before** starting.

## Identity verification

Multi-agent coordination on a single machine has three distinct identity paths, each with its own control:

**(a) `gh` API writes (issue comments, PR reviews, `gh api` POST/PATCH/DELETE).** The `gh` CLI stores a single active account per host in each `GH_CONFIG_DIR`. In this repo, per-agent isolation is achieved via distinct `GH_CONFIG_DIR` values (Codex: `~/.config/gh-j2d3`; Claude: `~/.config/gh-5u6r054`), each holding an `--insecure-storage` file-based token (bypasses the macOS Keychain, which is shared across processes regardless of `GH_CONFIG_DIR`). Verify at session start and immediately before any GitHub write:

```bash
gh api user --jq .login
```

Expected: `5u6r054` when Claude posts, `j2d3` when Codex posts. If it does not match, resolve the mismatch before proceeding — do not post.

**(b) `git commit` author identity.** Pinned per clone in repo-local `.git/config`, not global config and not any shell hook that mutates on directory change. Verify before every commit:

```bash
git config --local user.name
git config --local user.email
```

Expected: `5u6r054` and `156010594+5u6r054@users.noreply.github.com` when Claude commits; `j2d3` and `86860+j2d3@users.noreply.github.com` when Codex commits.

**(c) Squash-merge commit author.** GitHub's squash-merge API uses the *PR author's profile primary email* by default — **not** the local git config, and **not** the merger's identity. This was the failure mode observed in the 2026-08-02 merge-attribution incident: local git config was noreply, but `gh pr merge` without `--author-email` accepted the profile default, requiring a history rewrite. The `hack/merge-pr.sh` wrapper (see next section) enforces `--author-email` unconditionally; the human's manual merges must include it explicitly.

**Structural note on isolation.** Separate git worktrees do *not* isolate global `gh` auth state on macOS, and per-agent `GH_CONFIG_DIR` does not isolate Keychain-backed tokens. The full multi-agent isolation on one machine requires: (i) per-agent isolated clones, (ii) per-agent `GH_CONFIG_DIR` with `gh auth login --insecure-storage`, (iii) per-clone SSH host aliases (`github.com-<user>`) so `git push` uses the intended key regardless of `gh` state, (iv) `--author-email` on every squash merge. Convenience wrappers on any given laptop that combine these are helpful shortcuts, not normative — a new contributor on a different machine must be able to satisfy the portable rules above with plain `gh` and `git`.

## Merges

The safe author-merge path is `./hack/merge-pr.sh <pr-number>`. It fails closed on every check:

- Authenticated `gh` login must equal the PR author (authors merge their own).
- Only `j2d3` and `5u6r054` are recognized; unknown authors are rejected.
- PR must be `OPEN`, non-draft, `MERGEABLE`.
- An `APPROVED` review must exist from the *paired agent* (`j2d3`-authored → requires `5u6r054`; `5u6r054`-authored → requires `j2d3`) on the *exact current head commit* — a stale approval on a superseded commit or an approval from any other user does not count.
- CI is checked explicitly via `/repos/O/R/commits/{sha}/check-runs`; every check must be `status=completed` with `conclusion in (success, neutral, skipped)`, and there must be at least one check-run.
- Merge is `--squash --delete-branch --match-head-commit --author-email <noreply>`, using the author-specific noreply email from the wrapper's built-in mapping.
- Post-merge, the wrapper polls (bounded, up to 20s) for the resulting commit's metadata and fails loudly if the author email does not match the expected noreply.

The wrapper applies uniformly to all PRs. Meta-tooling PRs additionally require an issue #6 notice before opening (per rule 5). The wrapper does not remove the human's ability to merge via any other mechanism — it exists to make the safe path the easy path, not to be the only path.

## Templates

The current claim, handoff, blocker, review, and disagreement templates live in **pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6)**. Copy the relevant template when commenting there. Templates evolve; the issue is authoritative.

Two template conventions worth preserving as we iterate on issue #6:

- **Claim template includes a `Lease expires:` field.** Claims that pass their lease without a renewal or handoff comment are treated as released, so an interrupted agent does not leave a workstream apparently occupied indefinitely.
- **Disagreement template** captures: decision under dispute, position A + evidence, position B + evidence, shared assumptions, safety consequence, reversibility, recommended experiment, human decision. Turns disagreement into reusable engineering evidence rather than a private converge.

## Attribution in commits and PRs

Commits authored with the assistance of an AI agent include an `AI-Assisted-By` trailer identifying the tool:

- Claude Code commits: `AI-Assisted-By: Anthropic Claude Code`
- Codex commits: `AI-Assisted-By: OpenAI Codex`

Model-level co-authorship trailers (e.g. `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`) may accompany the `AI-Assisted-By` trailer for provenance but do not substitute for it.

PR descriptions authored primarily by an AI agent should identify the agent in the first line of the body (e.g. "*Drafted by Claude Code.*").

## Escalation

- **Disagreement on a technical decision between agents** — tag `@j2d3` in the PR thread using the disagreement template. Do not converge privately.
- **Uncertainty about safety** — pause the specific mutation, deployment, signing path, or merge that the uncertainty affects. Read-only diagnosis, tests, documentation, safe independent work, and a draft PR for review remain allowed. Post the uncertainty to issue #6 and wait for human input on the affected path.
- **Broken shared tooling** (CI, hooks, auth, upstream repo state) — post to issue #6 and stop the operation that depends on that tooling. Unrelated workstreams may continue.

## Known asymmetries and priority follow-ups

**Codex still operates under the human's `j2d3` account.** Claude was isolated onto the collaborator account `5u6r054`, but Codex's actions (git commits, `gh` API writes, review approvals) share the human's primary account. GitHub's audit trail cannot distinguish "human as `j2d3`" from "Codex as `j2d3`" for review-approval actions. Merges are attributable via `hack/merge-pr.sh`'s post-merge check, but reviews are not. The clean fix is a dedicated GitHub identity for Codex (e.g. `codex-j2d3-agent`), invited as `Write` collaborator with its own `GH_CONFIG_DIR` — the mirror of the `5u6r054` setup. Priority: high; targeted as the next COLLABORATION.md v3.

**Historical PR refs on GitHub retain pre-rewrite objects.** The 2026-08-02 history rewrites cleansed normal branches, but GitHub caches PR heads for a period. Complete server-side purge of the two previously-exposed personal profile addresses requires a GitHub Support ticket to dereference the affected `refs/pull/*` refs. Priority: medium; tracked outside this document.

**Branch protection is a doc rule until enforced.** The rules encoded here rely on the human enabling branch protection on `main` (require 1+ approving review, require code owner review, require CI to pass, require linear history). Without branch protection, the rules degrade to discipline. Priority: high; the enabling `gh api` command is documented in the v2 bundle PR body.

## What this document does not do

- It does not replace `CONTRIBUTING.md`, which governs branch, PR, and review process for humans.
- It does not replace the PRD (`docs/prd/`) as the product/architecture contract.
- It does not by itself authorize any AI agent to merge, sign, publish, deploy, or spend credentials — explicit user authorization for each such operation remains required, and the human retains override on all of it.
