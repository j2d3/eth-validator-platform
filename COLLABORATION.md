# Collaboration on this repository

Humans and AI agents share this repository. This document is the stable reference for how we coordinate. The live coordination channel is **pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6)** — this file describes the model; the issue is where it happens.

## Roles

| Actor | GitHub identity | Branch prefix | Merge authority |
|---|---|---|---|
| John Durkin (owner) | `j2d3` | any | Sole merger |
| Codex (OpenAI CLI) | `j2d3` (John's session) | `codex/*` | None; opens PRs, reviews Claude's |
| Claude Code (Anthropic CLI) | `5u6r054` (collaborator, Write) | `claude/*` | None; opens PRs, reviews Codex's |

## Coordination channels

Each GitHub primitive has exactly one job.

| Channel | Purpose |
|---|---|
| Pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6) | Claim work, hand off, request review, flag blockers |
| Draft pull requests | Visible work in progress |
| Ready pull requests | Ready-for-review artifacts; the *other* agent reviews before John merges |
| PR comments | Handoffs, cross-review, technical discussion tied to a diff |
| ADRs (`docs/adrs/`) | Durable architecture decisions |
| PRD (`docs/prd/`) | Product/architecture baseline; changes require a PR that names what shifted |

## Rules

1. **John is the only merger** unless he explicitly overrides.
2. **No self-approval, no self-merge.** Codex reviews Claude's PRs; Claude reviews Codex's.
3. **Every PR names the PRD section, safety invariant, phase-exit criterion, or operational hygiene rule it satisfies.** If none applies, that itself is a question to raise on issue #6 before proceeding.
4. **Disagreements between agents surface to John** rather than being resolved privately. The disagreement is the signal that makes two AIs valuable.
5. **Meta-tooling changes** (CI, `.github/`, hooks, Terraform apply, secret handling) are flagged to John explicitly on issue #6 rather than merged via ordinary flow.
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

## Identity verification before commit

The local shell hook (`~/.zshrc`, `switch_github_account_for_repo`) auto-selects a git identity based on the remote URL. This repo's URL is `j2d3/eth-validator-platform`, so the hook classifies it as `j2d3` and rewrites repo-local `.git/config` on every `chpwd`. **A Claude session that opens or resumes in this repo may find its identity has been silently flipped back to `j2d3`.**

**Before every commit, verify:**

```bash
gh auth status --hostname github.com | head -15
git config --local user.name
git config --local user.email
```

Expected settings when Claude commits:

```
user.name  = 5u6r054
user.email = 156010594+5u6r054@users.noreply.github.com
```

Expected settings when Codex commits: `j2d3` and John's j2d3 email.

If the identity is wrong, run `ghw` (Claude → 5u6r054) or `ghp` (Codex → j2d3), then re-set `git config --local user.email` explicitly — the `ghw` case of the shell function does not currently set the email, only the name.

## Templates

The current claim, handoff, blocker, and review templates live in **pinned issue [#6](https://github.com/j2d3/eth-validator-platform/issues/6)**. Copy the relevant template when commenting there. Templates evolve; the issue is authoritative.

## Escalation

- **Disagreement on a technical decision between agents** — tag `@j2d3` in the PR thread with both positions summarized. Do not converge privately.
- **Uncertainty about safety** — do not push a fix. Open an issue (or comment on issue #6) and wait.
- **Broken shared tooling** (CI, hooks, auth, upstream repo state) — post to issue #6 and stop your workstream until resolved.

## What this document does not do

- It does not replace `CONTRIBUTING.md`, which governs branch, PR, and review process for humans.
- It does not replace the PRD (`docs/prd/`) as the product/architecture contract.
- It does not authorize any AI agent to merge, sign, publish, deploy, or spend credentials.
