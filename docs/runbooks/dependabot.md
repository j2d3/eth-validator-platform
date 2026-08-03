# Dependabot: version updates, alerts, and automated security fixes

Dependabot reaches this repository through two independent surfaces that are
routinely conflated. They are configured in different places, changed by
different actors, and verified by different commands.

| | Version updates | Alerts | Automated security fixes |
|---|---|---|---|
| Where it lives | `.github/dependabot.yml` | Repository API state | Repository API state |
| Changed by | A reviewed pull request | Repository admin | Repository admin |
| In source control | Yes | **No** | **No** |
| Reviewable in a diff | Yes | No | No |
| What it does | Opens scheduled PRs to keep declared manifests current | Surfaces known advisories against detected manifests | Opens PRs for alerts that have a fixed version |

The practical consequence: **a pull request cannot turn alerts or security
fixes on or off, and this runbook does not ask anyone to try.** Editing
`.github/dependabot.yml` changes the scheduled-update half only. Equally, a
manifest absent from `.github/dependabot.yml` is still covered by alerts and
security fixes — coverage there follows GitHub's dependency detection, not this
file.

## What the configuration declares

Only ecosystems and directories that exist on `main`:

| Ecosystem | Directory | Manifest it reads | Day | Open-PR limit |
|---|---|---|---|---|
| `github-actions` | `/` | `.github/workflows/*.yaml` | Monday | 3 |
| `terraform` | `/terraform/bootstrap` | `*.tf`, `.terraform.lock.hcl` | Tuesday | 2 |
| `terraform` | `/terraform/environments/dev` | `*.tf`, `.terraform.lock.hcl` | Wednesday | 2 |
| `pip` | `/` | `requirements-dev.txt` | Thursday | 2 |
| `npm` | `/control-plane/portal` | `package.json`, `package-lock.json` | Friday | 2 |

Eleven open pull requests is the declared ceiling, weekly, staggered across five
days. Every commit is prefixed `chore(...)`. Within each block, minor and patch
updates are grouped into a single pull request while **majors are excluded from
the group** so each arrives alone — an actions major can change runner or input
contracts, and a Terraform provider major can change resource defaults against
a live EKS foundation.

Deliberately absent: `docker` (no Dockerfile; client images are digest-pinned and gated by
`tools/verify_container_contracts.py` plus the `CONTRIBUTING.md` release-note
review), and `helm` (no chart dependencies, and Dependabot has no Helm
ecosystem).

`tests/test_dependabot_contracts.py` holds this table against the filesystem in
both directions: an entry naming a directory that does not exist fails, and a
Terraform root, workflow directory, or root requirements file with no entry
fails. The config cannot drift from the tree without `make check` going red.

## Verifying the two enabled settings

There is an asymmetry here that will otherwise cost someone an afternoon:
**the two canonical endpoints are admin-only.** For a Write-level collaborator
they return `404` — the same status GitHub returns when the setting is genuinely
disabled. A Write-level agent reading `404` as an outage is reading a
permissions artifact.

Set the account first. Never inline a token, and never paste command output
containing one.

```bash
export GH_CONFIG_DIR="$HOME/.config/gh-<your-handle>"
gh api user --jq .login        # confirm the identity before trusting any result below
gh api /repos/j2d3/eth-validator-platform --jq .permissions.admin
```

### Admin path (authoritative for both settings)

```bash
# Vulnerability alerts: HTTP 204 = enabled, HTTP 404 = disabled.
gh api -i /repos/j2d3/eth-validator-platform/vulnerability-alerts | head -1

# Automated security fixes: {"enabled": true, "paused": false}
gh api /repos/j2d3/eth-validator-platform/automated-security-fixes
```

### Non-admin path (authoritative for alerts only)

Listing alerts does **not** require admin, but it is **not granted by
repository role either.** Collaborator role and token permission are separate
axes, and conflating them is how someone ends up handing a portal a credential
that cannot read what they promised it would. Per GitHub's
[Dependabot alerts REST permissions](https://docs.github.com/en/rest/dependabot/alerts),
the endpoint requires:

| Credential type | Least-privilege permission |
|---|---|
| Fine-grained token / GitHub App | **`Dependabot alerts: read`** |
| Classic OAuth token or PAT | **`security_events`** (or `public_repo` for public repositories) |

Grant that permission explicitly. A token is not entitled to this endpoint
merely because its bearer is a Write collaborator — Write is necessary for the
repository, not sufficient for the security API.

The failure mode is otherwise unambiguous. Per GitHub's documented behavior a
repository with alerts disabled returns `403` with an explicit message, so a
`200` here is positive confirmation rather than an absence of evidence:

```bash
# HTTP 200 (possibly an empty array) => alerts enabled. Prints the open count.
gh api /repos/j2d3/eth-validator-platform/dependabot/alerts --jq 'length'

# HTTP 403 "Dependabot alerts are disabled for this repository." => disabled.
```

This is the only endpoint of the three that yields a **live number** without
admin, which makes it the one a control-plane portal can actually consume —
provided its credential carries the alert-read permission above. The
`403`-when-disabled branch is GitHub's
documented behavior and has not been exercised here — disabling the setting to
observe it is not a test anyone should run.

### Observed state

| Fact | Value | Source | When |
|---|---|---|---|
| `PUT /vulnerability-alerts` | HTTP `204` | `j2d3`, repository admin — **attested, not independently verifiable from a collaborator account** | 2026-08-03 |
| `GET /automated-security-fixes` | `enabled=true` | `j2d3`, repository admin — attested | 2026-08-03 |
| `GET /dependabot/alerts` | HTTP `200`, `[]` — **0 open alerts** | `5u6r054`, non-admin; **this token has effective alert-read access** — directly observed | 2026-08-03 |
| `GET /repos/...` → `permissions.admin` | `false` for `5u6r054` | directly observed | 2026-08-03 |

The third row is the useful one: it independently corroborates that alerts are
**on**, from an account that cannot read the admin endpoints at all. The first
two rows remain admin-attested and should be described that way in any handoff.

Read the third row narrowly. It is evidence that **this particular credential**
is effectively authorized for the alerts endpoint — not that Write collaborators
in general are, and not that any one scope string is the entitlement. When
provisioning a new token, grant `Dependabot alerts: read` (or `security_events`)
deliberately and re-observe a `200` rather than assuming role carries it.

There is **no non-admin read for automated security fixes.** From a Write-level
account that setting can only be taken as attested by an admin, and any
handoff claiming otherwise is overstating its evidence. Say "admin-attested",
not "verified".

Open alerts in the browser — authorized GitHub access required:

```
https://github.com/j2d3/eth-validator-platform/security/dependabot
```

### One-shot check

```bash
gh api user --jq .login
gh api /repos/j2d3/eth-validator-platform --jq \
  '{admin: .permissions.admin, security_and_analysis}'
gh api /repos/j2d3/eth-validator-platform/dependabot/alerts --jq \
  '{open_alerts: length}'
gh api -i /repos/j2d3/eth-validator-platform/vulnerability-alerts | head -1
gh api /repos/j2d3/eth-validator-platform/automated-security-fixes
```

Read it as: `security_and_analysis: null` together with `admin: false` means the
last two lines are uninformative by permission, not by configuration.

## Labels are declared here and exist in the repository

`.github/dependabot.yml` references `dependencies`, `ci`, `terraform`, and
`python`. All four were **created and verified present on 2026-08-03** by
`j2d3`, using the repository-admin boundary, with the colors and descriptions
declared below. Dependabot will now apply them.

| Label | Color | Description | Verified |
|---|---|---|---|
| `dependencies` | `#0366d6` | Dependency updates | 2026-08-03 |
| `ci` | `#1d76db` | CI and workflow tooling | 2026-08-03 |
| `terraform` | `#5c4ee5` | Terraform roots and providers | 2026-08-03 |
| `python` | `#3572a5` | Python validation dependencies | 2026-08-03 |

Before that date the label set was still GitHub's default nine, so the two
blocks predating this runbook had been labelling nothing since `93aca03` —
Dependabot silently drops a label that does not exist rather than failing.

Labels are repository state, not source control, so this is the one part of the
setup a pull request cannot carry: the commands below are the repair path, not
something `make check` can restore. They cost triage convenience only — nothing
in CI, branch protection, or the merge path keys off these labels, and a missing
label never blocks or hides an update.

The commands are idempotent (`--force` updates an existing label in place), so
they are safe to re-run to repair drift or to seed a fork:

```bash
gh label create dependencies --repo j2d3/eth-validator-platform \
  --color 0366d6 --description "Dependency updates" --force
gh label create ci --repo j2d3/eth-validator-platform \
  --color 1d76db --description "CI and workflow tooling" --force
gh label create terraform --repo j2d3/eth-validator-platform \
  --color 5c4ee5 --description "Terraform roots and providers" --force
gh label create python --repo j2d3/eth-validator-platform \
  --color 3572a5 --description "Python validation dependencies" --force
```

Verify:

```bash
gh label list --repo j2d3/eth-validator-platform \
  --json name --jq '[.[].name] | map(select(. == "dependencies" or . == "ci"
    or . == "terraform" or . == "python"))'
```

`tests/test_dependabot_contracts.py` asserts that every label referenced in the
config is documented above, so the two cannot drift apart.

## Merging a Dependabot pull request

**`./hack/merge-pr.sh` will refuse a Dependabot PR, by design.** The wrapper
recognizes exactly three authors — `j2d3`, `5u6r054`, and
`github-actions[bot]` — and fails closed on anything else:

```
unknown PR author 'dependabot[bot]'; wrapper recognizes only j2d3, 5u6r054, and github-actions[bot]
```

This is correct fail-closed behavior, not a defect, and no one should work
around it by widening the allowlist casually: the wrapper's author mapping also
drives the mandatory `--author-email` and the paired-reviewer requirement, and
`dependabot[bot]` has a different attribution and review story from either
agent. Until that is deliberately designed, use the **exact-head CLI rebase
path** below.

**Do not merge these in the web UI.** The UI offers no exact-head guard and its
squash path selects a merge-commit author from profile metadata — the specific
failure that produced the 2026-08-02 merge-attribution incident and drove the
wrapper work in the first place (see [COLLABORATION.md](../../COLLABORATION.md)).
A Dependabot PR is merged from the CLI by an operator holding merge rights —
`j2d3`, or Codex acting as `j2d3` — after the required review and green checks:

```bash
export GH_CONFIG_DIR="$HOME/.config/gh-j2d3"
REPO=j2d3/eth-validator-platform
PR=<pr-number>

# 1. Confirm the author is who you think it is, and capture the exact head.
gh pr view "$PR" --repo "$REPO" \
  --json author,headRefOid,reviewDecision,mergeStateStatus,commits \
  --jq '{author: .author.login, head: .headRefOid, review: .reviewDecision,
         state: .mergeStateStatus, commits: (.commits | length)}'

# 2. Merge at that exact SHA. Rebase — never squash.
gh pr merge "$PR" --repo "$REPO" \
  --rebase \
  --delete-branch \
  --match-head-commit <head-sha-from-step-1>
```

`--rebase` is the load-bearing choice, and it is the same mode
`hack/merge-pr.sh` already uses for `github-actions[bot]`: it replays the
bot-authored commit as-is, preserving `dependabot[bot]` as the commit author
without inventing a squash-author email. That is why no `--author-email` appears
here — passing one would be meaningless on a rebase, and squashing would force
GitHub to pick an author from profile metadata, which is exactly the control
this repository has already paid to establish.

`--match-head-commit` fails the merge if the head moved after review — Dependabot
force-pushes its branches when it rebases them, so this is a live risk here, not
a formality. If the flag rejects the merge, re-review the new head rather than
re-running with the newer SHA.

Refuse the merge if the PR carries more than one commit, or if any commit is not
authored by `dependabot[bot]` — a rebase replays whatever is there, so an
unexpected commit rides in with it. This mirrors the single-commit and
author-verification checks the wrapper enforces on the automation path.

Branch protection still applies in full — three green CI checks, CODEOWNERS
approval, linear history.

Folding this into `hack/merge-pr.sh` as a fourth recognized author is a
deliberate design change — it needs an attribution and paired-reviewer story for
`dependabot[bot]` — and belongs in its own pull request, not here.

Reviewing one is ordinary review, with the repository's own rules on top:

1. Read the upstream changelog. For a client image this is required by
   `CONTRIBUTING.md`; Dependabot does not manage images here, but the same
   habit applies to providers and actions.
2. Confirm CI is green on the exact head.
3. For Terraform provider bumps, confirm `make validate` passes and that the
   plan is a no-op or an understood diff. A provider major can change resource
   defaults; those do not merge on a green check alone.
4. Nothing in a dependency bump may weaken the signing invariants in PRD §5.

## Pausing or scoping updates

To stop a single ecosystem, remove or comment out its block in
`.github/dependabot.yml` via a pull request. To stop a specific dependency
while keeping the rest current, add an `ignore` entry to that block. Prefer a
narrow `ignore` over deleting a block: a deleted block silently stops all
currency for that manifest, whereas an `ignore` records what was suppressed and
why.

Note that `ignore` affects **version updates only**. A security fix for an
ignored dependency still opens a pull request, which is the intended behavior.

## Surfacing this in the control-plane portal

The portal is expected to show Dependabot configuration and state with a deep
link to GitHub. The same split this runbook opens with decides what it may
claim:

- **Configuration** — the ecosystem/directory/cadence/limit table above — is
  read from `.github/dependabot.yml` in the tree. It needs no credential, it is
  exact, and it is the portal's safe default rendering.
- **Alert count** is live API state and needs an authenticated provider.
  `GET /repos/j2d3/eth-validator-platform/dependabot/alerts` is the endpoint.
  Admin is **not** required — but the credential must explicitly carry
  `Dependabot alerts: read` (fine-grained) or `security_events` (classic).
  Provision that permission deliberately; do not assume a token inherits it
  from the bearer's Write role, and confirm with a `200` before shipping a
  panel that depends on it.
- **Alerts-enabled and security-fixes-enabled** are admin-only. A portal
  running with a non-admin credential receives `404` on both and **must render
  that as "unknown — insufficient permission", never as "disabled".** Rendering
  a permissions artifact as a disabled security control is the specific failure
  this runbook exists to prevent, and it is worse in a dashboard than in a
  terminal, because a dashboard is believed at a glance.

Deep link for the human-facing path (authorized GitHub access required):

```
https://github.com/j2d3/eth-validator-platform/security/dependabot
```
