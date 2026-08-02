# Collaboration on this repository

Humans and AI agents share this repository. This document is the stable reference for how we coordinate. The live coordination channel is **pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6)** — this file describes the model; the issue is where it happens.

## Roles

| Actor | GitHub identity | Branch prefix | Merge authority |
|---|---|---|---|
| the human (owner) | `j2d3` | any | Sole merger |
| Codex (OpenAI CLI) | `j2d3` (the human's session) | `codex/*` | None; opens PRs, reviews Claude's |
| Claude Code (Anthropic CLI) | `5u6r054` (collaborator, Write) | `claude/*` | None; opens PRs, reviews Codex's |

## Coordination channels

Each GitHub primitive has exactly one job.

| Channel | Purpose |
|---|---|
| Pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6) | Claim work, hand off, request review, flag blockers |
| Draft pull requests | Visible work in progress |
| Ready pull requests | Ready-for-review artifacts; the *other* agent reviews before the human merges |
| PR comments | Handoffs, cross-review, technical discussion tied to a diff |
| ADRs (`docs/adrs/`) | Durable architecture decisions |
| PRD (`docs/prd/`) | Product/architecture baseline; changes require a PR that names what shifted |

## Rules

1. **The human is the only merger** unless they explicitly override.
2. **No self-approval, no self-merge.** Codex reviews Claude's PRs; Claude reviews Codex's.
3. **Every PR names the PRD section, safety invariant, phase-exit criterion, or operational hygiene rule it satisfies.** If none applies, that itself is a question to raise on issue #6 before proceeding.
4. **Disagreements between agents surface to the human** rather than being resolved privately. The disagreement is the signal that makes two AIs valuable.
5. **Meta-tooling changes** (CI, `.github/`, hooks, Terraform apply, secret handling) use the ordinary PR flow *and* require explicit notice to the human on issue #6 plus explicit human approval before merge.
6. **Fail-closed for signing.** Nothing in local scripts, CI, or GitHub automation may weaken the safety invariants in PRD §5.
7. **No secrets** — no credentials, keys, mnemonic material, keystore passwords, customer data, or secret values in issue #6, PR bodies, comments, commit messages, or logs.

## Start-of-work checklist

Run these before editing files, opening a PR, or resuming work after a break:

```bash
git fetch origin --prune
gh issue list --state open
gh pr list --state open
gh pr status
```

Then read pinned issue #6 for active claims and inspect open PRs for overlapping paths. Post a claim comment on issue #6 using the template there **before** starting.

## Identity verification before GitHub writes and git commits

The `gh` CLI stores a single active account per host in a shared config file. Any `gh auth switch` affects every process sharing that config, and on macOS `gh`'s default Keychain-backed token storage is shared across processes even when `GH_CONFIG_DIR` is set to isolated directories. This was observed during review of this document's own PR: a Codex claim comment was attributed to Claude's identity because a preceding switch had crossed the two agents' auth state.

**The portable rule for GitHub writes.** Immediately before every `gh` command that mutates GitHub state (issue comment, PR create/comment/review, `gh api` with `-X POST/PATCH/DELETE`), verify the active account matches the intended agent:

```bash
gh api user --jq .login
```

Expected: `5u6r054` when Claude posts, `j2d3` when Codex posts. If it does not match, resolve the mismatch before proceeding — do not post.

**The portable rule for git commits.** Pin identity per clone (or per worktree if `extensions.worktreeConfig` is enabled) using repo-local config, not global config and not any shell hook that mutates on directory change. Verify before every commit:

```bash
git config --local user.name
git config --local user.email
```

Expected: `5u6r054` and `156010594+5u6r054@users.noreply.github.com` when Claude commits; the human's chosen j2d3 identity when Codex commits.

**Structural note on isolation.** Separate git worktrees do *not* isolate global `gh` auth state. The clean multi-agent solution on one machine is per-agent isolated clones plus per-agent `GH_CONFIG_DIR` (with `gh auth login --insecure-storage` on macOS to avoid the shared Keychain), plus per-clone SSH host aliases (`github.com-<user>`) so `git push` uses the intended key regardless of `gh` state. Convenience wrappers on any given laptop that combine those are helpful shortcuts, not normative — a new contributor on a different machine must be able to satisfy the portable rules above with plain `gh` and `git`.

## Templates

The current claim, handoff, blocker, and review templates live in **pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6)**. Copy the relevant template when commenting there. Templates evolve; the issue is authoritative.

## Attribution in commits and PRs

Commits authored with the assistance of an AI agent include an `AI-Assisted-By` trailer identifying the tool:

- Claude Code commits: `AI-Assisted-By: Anthropic Claude Code`
- Codex commits: `AI-Assisted-By: OpenAI Codex`

Model-level co-authorship trailers (e.g. `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`) may accompany the `AI-Assisted-By` trailer for provenance but do not substitute for it.

PR descriptions authored primarily by an AI agent should identify the agent in the first line of the body (e.g. "*Drafted by Claude Code.*").

## Escalation

- **Disagreement on a technical decision between agents** — tag `@j2d3` in the PR thread with both positions summarized. Do not converge privately.
- **Uncertainty about safety** — pause the specific mutation, deployment, signing path, or merge that the uncertainty affects. Read-only diagnosis, tests, documentation, safe independent work, and a draft PR for review remain allowed. Post the uncertainty to issue #6 and wait for human input on the affected path.
- **Broken shared tooling** (CI, hooks, auth, upstream repo state) — post to issue #6 and stop the operation that depends on that tooling. Unrelated workstreams may continue.

## What this document does not do

- It does not replace `CONTRIBUTING.md`, which governs branch, PR, and review process for humans.
- It does not replace the PRD (`docs/prd/`) as the product/architecture contract.
- It does not by itself authorize any AI agent to merge, sign, publish, deploy, or spend credentials — explicit user authorization for each such operation remains required.
