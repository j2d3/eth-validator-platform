# Set up a two-agent GitHub workflow

This guide describes a reusable workflow for two coding-agent sessions working
on one repository under two GitHub identities. It applies to two Codex
instances, two Claude Code instances, or one of each.

The two identities improve attribution and make cross-review enforceable. They
do not create two accountable humans: the repository owner remains responsible
for both agents and for every credential, merge, deployment, and external
side effect.

## 1. Prepare the repository and identities

Use two GitHub accounts controlled by the human operator. Give the second
account only the repository permission it needs. For a private repository,
`Write` is normally enough to push branches and review pull requests; keep
administration with the owner.

On a shared workstation, use a separate clone, GitHub CLI configuration, SSH
key, and repo-local commit identity for each lane:

```text
~/work/project-agent-a/       GH_CONFIG_DIR=~/.config/gh-agent-a
~/work/project-agent-b/       GH_CONFIG_DIR=~/.config/gh-agent-b
```

Configure each clone:

```bash
# Run in the appropriate clone; substitute that lane's values.
git config --local user.name "<github-login>"
git config --local user.email "<github-noreply-email>"
git remote set-url origin git@github.com-<github-login>:<owner>/<repo>.git

GH_CONFIG_DIR="$HOME/.config/gh-<github-login>" gh auth login \
  --hostname github.com --git-protocol ssh --insecure-storage

GH_CONFIG_DIR="$HOME/.config/gh-<github-login>" gh api user --jq .login
git config --local user.email
```

`--insecure-storage` means the token is stored in the selected config
directory rather than the shared macOS Keychain. Protect that directory as a
credential. The reason for using it here is isolation: separate
`GH_CONFIG_DIR` values do not isolate Keychain-backed tokens on the same Mac.

Give each SSH key a distinct host alias in `~/.ssh/config`, for example
`github.com-agent-a` and `github.com-agent-b`, and register each public key
with the corresponding account. This makes `git push` independent of whichever
account `gh` last selected.

Use distinct branch prefixes such as `agent-a/*` and `agent-b/*`. Set both
accounts to use GitHub noreply email addresses and enable GitHub's privacy and
push-protection settings.

For two Codex lanes, also launch each lane with a different `CODEX_HOME` root.
That directory holds Codex configuration, authentication, logs, sessions, and
skills, and it must exist before Codex starts. Keep shared repository rules in
the repo's `AGENTS.md` or collaboration document; keep persona-specific state
in the lane's Codex home. Two Claude Code lanes need the equivalent separation
for Claude's account, configuration, permission, and session state.

Separate clones and configuration directories prevent accidental identity
mixing. They are not a hostile-process security boundary. Use separate OS
users or isolated hosts if one lane must be unable to read the other's local
credentials.

## 2. Add structural controls

Protect `main` with:

- pull requests required;
- required CI checks;
- stale approvals dismissed after a push;
- linear history;
- no force-push or branch deletion;
- review required from the other GitHub identity.

Add `CODEOWNERS` for both accounts. Add a repository merge wrapper rather than
having agents call `gh pr merge` directly. The wrapper should verify:

1. the caller is the PR author;
2. the PR is open, current with `main`, and mergeable;
3. the paired account's latest review is `APPROVED` on the exact current head;
4. every required check has completed successfully;
5. the squash merge uses the PR author's noreply address;
6. the resulting commit has the expected author metadata.

The wrapper in this repository is [`hack/merge-pr.sh`](../../hack/merge-pr.sh).
It is project-specific, but those six checks are portable.

An approval tied only to a commit ID can survive a force-push that removes and
then restores that commit. A stricter wrapper also compares review time with
the latest force-push event for the PR.

## 3. Use GitHub as the shared state

Use one pinned coordination issue or a small set of labeled issues. A claim
contains the issue, branch, file surface, expected result, prohibited actions,
and a lease expiration. The lease prevents an interrupted session from
occupying a workstream indefinitely.

The normal cycle is:

```text
claim issue and file surface
  -> build on an isolated branch
  -> open one PR with tests and explicit boundaries
  -> paired agent reviews the exact head
  -> author amends or merges through the wrapper
  -> record runtime evidence when the change has runtime claims
  -> take the next unclaimed task
```

PRs, reviews, issue comments, and CI are authoritative. Local scratch files
can carry a quick narrative handoff, but two separate clones do not share a
file automatically. If scratch files are used, define both absolute paths and
copy/update them deliberately; never let a stale local note override GitHub.

Before every GitHub write or commit, verify the lane:

```bash
GH_CONFIG_DIR="$GH_CONFIG_DIR" gh api user --jq .login
git config --local user.name
git config --local user.email
```

Keep work in progress bounded: one authored open PR per lane is a useful
default. Review findings that affect correctness, safety, or the agreed
contract block the PR; style preferences and optional polish become follow-up
issues. This prevents review from creating work faster than the pair can land
it.

## 4. Define the human boundary

Write the boundary in the initial prompt and repository instructions. A useful
default is:

- agents may inspect, edit, test, push branches, open PRs, review, and merge
  low-risk repository changes through the guarded path;
- deployments, paid cloud changes, credentials, secrets, key generation,
  money movement, destructive data operations, and production mutations need
  explicit human authorization;
- a blocker pauses only the affected operation; unrelated safe work continues;
- an unresolved material design choice returns to the human with options and
  evidence.

Keep auto-merge narrower than ordinary PR creation. A supervisor can leave
infrastructure, secret-handling, deployment, or live-reconciled paths for an
interactive decision even when review and CI are green.

## 5. Make unattended operation bounded

A local supervisor can poll GitHub every minute and perform four actions:

1. dispatch the paired reviewer for an unreviewed exact head;
2. return `REQUEST_CHANGES` to the author session;
3. author-merge an approved, green, low-risk PR through the wrapper;
4. when no PR is open, take the next bounded repo-only task from a queue.

Run each worker in a temporary worktree with a maximum duration. Verify the
GitHub side effect rather than trusting the worker's final prose: a review must
exist, a branch must be pushed, or a PR must be open. A worker can complete a
review and still fail to exit.

Do not assume a sandboxed worker can read the host's Keychain token or access
the network. Give the worker only the inputs it needs, let it commit and push
through the lane's SSH identity, and let the trusted host process perform the
GitHub API call when necessary. Never pass a credential in a prompt or write it
to a handoff file.

The task queue is what distinguishes unattended progress from unattended
polling. Without a queue, "no open PR" becomes an idle state.

An interactive terminal prompt cannot guarantee this loop remains alive after
the session ends. Truly unattended operation needs an external supervisor or
product scheduler with timeouts, durable queue state, and restart behavior.
In this experiment the supervisor proved one review-and-merge cycle; it did
not establish long-term daemon reliability.

## 6. Choose the pair

| Pair | Advantages | Costs and risks |
|---|---|---|
| Two Codex instances | Same CLI, sandbox, instruction format, and operational behavior; easiest supervisor implementation; independent contexts can still produce different designs | Same model family and defaults can repeat the same blind spot; audit trail needs two GitHub identities because vendor identity alone is not visible in GitHub |
| Two Claude Code instances | Same tool and permission model; simple shared conventions; useful when an existing team already standardizes on Claude Code | Same-model correlation; plan/permission behavior and account limits can stop both lanes together; use explicit leases to prevent duplicate work after a session restarts |
| Codex + Claude Code | Different model/tool behavior often produces more independent review; one tool may catch assumptions the other accepts | More setup and prompting differences; separate quota/failure modes; direct CLI invocation starts a fresh agent, not the interactive session; coordination must stay in GitHub |

For all three, diversity comes mainly from independent context, a reviewer
prompt that starts from the diff, and evidence requirements. Merely running
the same prompt twice is weaker than assigning builder and reviewer roles.
Using different models or reasoning profiles can add some diversity within a
same-vendor pair, but shared provider outages, quotas, and defaults remain
correlated.

One Codex session with subagents is a different pattern. Subagents are useful
for bounded exploration, tests, and independent analysis, and reduce context
noise in the main thread. They share the parent workflow and do not supply a
second GitHub review identity. Use subagents inside either lane; use two
top-level lanes when cross-review attribution is the goal.

### Two-Codex launch notes

Two OpenAI accounts are not required for GitHub attribution. Two top-level
Codex sessions can use the same OpenAI account while keeping separate
`CODEX_HOME` roots, clones, and GitHub personas. They will still share account
quota and provider failure modes; separate subscriptions or API credentials
change that operational coupling, not the GitHub review contract.

Put durable repository conventions in `AGENTS.md`, which Codex loads when a
session starts. For a bounded supervisor-dispatched worker, use a fresh
noninteractive process such as:

```bash
codex exec --ephemeral --sandbox workspace-write "<bounded worker prompt>"
```

`--ephemeral` avoids persisting that worker's session rollout. The supervisor
must still select the correct lane-specific Codex home and clone before the
process starts, apply a timeout, and verify the resulting GitHub artifact.

## Repository-bootstrap prompt

Give this prompt to one agent before starting product work. It creates the
repository-side contract but does not authenticate either persona or grant
itself authority:

```text
Prepare this repository for a two-agent, two-GitHub-persona development loop.
You are the bootstrap agent only. Do not authenticate accounts, create
credentials, push, merge, deploy, or begin product work.

First inspect the repository and its existing contribution rules. Then prepare
one focused change that adds or proposes:

1. A tool-neutral collaboration document defining two GitHub personas and
   branch prefixes; no self-approval; exact-head paired review; author-owned
   guarded merges; GitHub issues/PRs as authoritative state; claim leases;
   WIP limit one; and human-only boundaries for credentials, deployments,
   infrastructure mutation, signing, spending, and scope expansion.
2. A parameterized merge wrapper that verifies authenticated identity, the
   designated paired reviewer's fresh approval on the current head, completed
   required CI, a current/mergeable branch, the author's noreply merge email,
   and post-merge attribution. A review submitted before the force-push that
   installed the current head must not count.
3. CODEOWNERS plus a branch-protection setup plan.
4. Tests for the merge wrapper's failure cases.
5. An optional bounded-supervisor design with temporary worktrees, timeouts,
   locks, WIP limits, and no live-system credentials.

Ask the human only for the repository name, two GitHub handles, noreply
addresses, clone paths, required checks, and paths that must never auto-merge.
Do not put credentials in tracked files.
```

After the human reviews and lands that setup, create the two isolated lanes
and use the following steady-state prompt in both sessions.

## Steady-state prompt for both agents

Give the following prompt to each interactive session, substituting the lane
values. The second session receives the same prompt with A/B reversed.

```text
You are agent <A> in a two-agent GitHub workflow for <owner>/<repo>.

Identity and workspace:
- GitHub login: <agent-a-login>
- GH_CONFIG_DIR: <absolute config directory>
- clone: <absolute clone path>
- branch prefix: <agent-a-prefix>/*
- paired reviewer: <agent-b-login>

At the start of every work item, fetch main, verify gh login and repo-local
noreply identity, inspect open issues/PRs, and claim one non-overlapping file
surface in the coordination issue. GitHub is authoritative shared state.

Keep at most one authored PR open in this lane. Before starting new work,
review a paired PR that is waiting on you or finish your existing PR.

Implement the smallest complete slice, run proportionate tests, open one PR,
and request review from the paired account. Never approve your own PR. After
the paired account approves the exact current head and CI is green, merge your
own PR only through <merge-wrapper>. After merge, take the next unclaimed task;
"no open PR" is not a stopping condition.

You may edit/test/push/review ordinary repository changes. Do not deploy,
spend cloud resources, access or expose secrets, generate keys, move funds,
delete durable data, or mutate production/live systems without explicit human
authorization. Pause only the affected operation when blocked and continue
unrelated safe work. Ask the human only for a real authority boundary or an
unresolved material design decision.

For reviews, begin from the exact diff and evidence rather than the author's
summary. Post APPROVE or REQUEST_CHANGES on the exact head. For every GitHub
write, verify that gh reports <agent-a-login> first.

Use precise runtime language: rendered, deployed, Ready, connected, synced,
signing, and qualified are separate claims and require separate evidence.

Record AI assistance honestly in commits and PRs using the repository's
attribution convention. Do not place credentials or personal data in prompts,
logs, commits, PRs, or coordination notes.
```

## Scoped unattended-worker prompt

Use a fresh process only for a bounded task; do not describe it as the same
interactive agent or assume it has that session's memory.

```text
Work on issue <N> in <owner>/<repo> at exact base <SHA>. Your branch is
<branch>; your GitHub identity is <login>; the paired reviewer is <other>.
Scope: <files and concrete outcome>. Prohibited: <live/cloud/secret/destructive
surfaces>. Inspect current repository instructions, implement the complete
bounded change, run proportionate tests, commit with the configured noreply
identity, push the branch, and open one PR linked to issue <N>. Do not merge.
Finish by producing the GitHub artifact, not only a summary.
```

The supervisor should still verify the resulting branch, PR, review, and
commit identities. Prompts express policy; GitHub protection and the merge
wrapper enforce it.

For a Claude-specific human checklist and a paste-ready symmetric prompt, see
[`two-claude-collaboration.md`](two-claude-collaboration.md) and
[`two-claude-agent-prompt.md`](two-claude-agent-prompt.md).
