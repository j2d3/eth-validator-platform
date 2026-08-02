#!/usr/bin/env bash
# Safe, author-driven CLI merge for j2d3/eth-validator-platform.
#
# Enforces the collaboration contract described in COLLABORATION.md:
#
#   1. Agent-authored PRs are merged by their authenticated author.
#      A github-actions[bot] lifecycle PR may be merged by either agent
#      because the ephemeral repository token cannot operate this local
#      wrapper.
#   2. Only j2d3, 5u6r054, and the lifecycle automation bot are recognized
#      authors; unknown authors fail closed. The wrapper enforces pairing:
#      j2d3-authored PRs require an approval from 5u6r054, and
#      5u6r054-authored PRs require an approval from j2d3. Automation PRs
#      require exact-head approvals from both agents.
#   3. The PR must be OPEN, non-draft, and mergeable.
#   4. Every required agent's LATEST review must be APPROVED on the exact
#      current head commit. Evaluating only the latest review from each
#      reviewer prevents a subsequent CHANGES_REQUESTED from being
#      silently ignored just because a historical APPROVED record still
#      exists. Belt-and-braces: the PR's aggregate reviewDecision must
#      also be APPROVED.
#   5. CI is checked explicitly via /repos/O/R/commits/{sha}/check-runs;
#      every check must be status=completed with conclusion in
#      (success, neutral, skipped), and there must be at least one.
#      Additionally, mergeStateStatus must be CLEAN — CI runs alone do
#      not detect a branch that is BEHIND main.
#   6. Agent-authored merge is squash with --match-head-commit and mandatory
#      --author-email set to the PR author's noreply address. This
#      addresses the class of failures observed in the 2026-08-02
#      merge-attribution incident, where GitHub's default squash-merge
#      behavior used the PR author's profile primary email instead of
#      a noreply address, requiring a history rewrite. A single-commit
#      automation PR is rebased instead, preserving its already-verified bot
#      noreply author while maintaining linear history.
#   7. Post-merge, the wrapper polls (bounded, up to 20s) for the
#      merged=true state before trusting merge_commit_sha (GitHub
#      populates that field pre-merge with a synthetic test-merge SHA),
#      then verifies the resulting commit's author email matches the
#      expected noreply. Fails loudly otherwise.
#
# The human's ability to merge via any other mechanism (GUI, plain
# gh pr merge, git merge) is intentionally OUTSIDE this wrapper. This
# script exists to make the safe path the easy path, not to be the
# only path.

set -euo pipefail

readonly REPO='j2d3/eth-validator-platform'

fail() {
  printf 'merge-pr.sh: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || fail "required command missing: $1"
}

# Recognized PR author -> mandatory --author-email mapping.
author_email_for() {
  case "$1" in
    j2d3)    printf '86860+j2d3@users.noreply.github.com' ;;
    5u6r054) printf '156010594+5u6r054@users.noreply.github.com' ;;
    'github-actions[bot]') printf '41898282+github-actions[bot]@users.noreply.github.com' ;;
    *)       return 1 ;;
  esac
}

# Recognized PR author -> reviewer(s) required by the collaboration contract.
required_reviewers_for() {
  case "$1" in
    j2d3)    printf '5u6r054' ;;
    5u6r054) printf 'j2d3' ;;
    'github-actions[bot]') printf 'j2d3\n5u6r054' ;;
    *)       return 1 ;;
  esac
}

is_agent_login() {
  [[ "$1" == 'j2d3' || "$1" == '5u6r054' ]]
}

# Bounded poll: retry a command up to 10 times (20s total) until it
# emits a non-empty, non-"null" value on stdout.
poll_nonempty() {
  local result
  local attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    result="$("$@" 2>/dev/null || true)"
    if [[ -n "$result" && "$result" != 'null' ]]; then
      printf '%s' "$result"
      return 0
    fi
    sleep 2
  done
  return 1
}

require_command gh
require_command jq

readonly PR_NUMBER="${1:-}"
if [[ -z "$PR_NUMBER" ]]; then
  printf 'Usage: %s <pr-number>\n' "$0" >&2
  exit 2
fi

# 1. Verify authenticated login.
active_login="$(gh api user --jq .login)" \
  || fail 'gh api user failed; check GH_CONFIG_DIR and gh auth status'
[[ -n "$active_login" ]] || fail 'empty active login'

# 2. Fetch PR metadata.
pr_json="$(gh pr view "$PR_NUMBER" --repo "$REPO" --json \
  author,commits,headRefName,headRefOid,state,isDraft,mergeable,mergeStateStatus,reviewDecision)" \
  || fail "gh pr view $PR_NUMBER failed"

pr_author="$(jq -r '.author.login' <<<"$pr_json")"
head_oid="$(jq -r '.headRefOid' <<<"$pr_json")"
state="$(jq -r '.state' <<<"$pr_json")"
is_draft="$(jq -r '.isDraft' <<<"$pr_json")"
mergeable="$(jq -r '.mergeable' <<<"$pr_json")"
merge_state="$(jq -r '.mergeStateStatus' <<<"$pr_json")"
review_decision="$(jq -r '.reviewDecision' <<<"$pr_json")"
commit_count="$(jq '.commits | length' <<<"$pr_json")"

# 3. Agent authors merge their own. The automation bot cannot run a local
#    wrapper, so either recognized agent may merge its PR after both review it.
if [[ "$pr_author" == 'github-actions[bot]' ]]; then
  is_agent_login "$active_login" \
    || fail "automation PR must be merged by j2d3 or 5u6r054, not $active_login"
else
  [[ "$active_login" == "$pr_author" ]] \
    || fail "authenticated login ($active_login) != PR #$PR_NUMBER author ($pr_author); switch GH_CONFIG_DIR to the author's config, or ask the author to merge"
fi

# 4. Known-author check + author noreply email + required reviewer set.
expected_email="$(author_email_for "$pr_author")" \
  || fail "unknown PR author '$pr_author'; wrapper recognizes only j2d3, 5u6r054, and github-actions[bot]"
required_reviewers="$(required_reviewers_for "$pr_author")" \
  || fail "unknown PR author '$pr_author'; cannot determine required reviewer(s)"

# 5. Basic PR state.
[[ "$state" == 'OPEN' ]]          || fail "PR state is $state (need OPEN)"
[[ "$is_draft" == 'false' ]]      || fail 'PR is a draft; mark it ready for review first'
[[ "$mergeable" == 'MERGEABLE' ]] || fail "PR mergeable=$mergeable (need MERGEABLE)"

# 6. Every required reviewer's LATEST review state must be APPROVED on the
#    exact current head, AND the PR's aggregate reviewDecision must be
#    APPROVED. Evaluating only the latest review prevents a later
#    CHANGES_REQUESTED from being masked by historical approval.
reviews_json="$(gh api "/repos/$REPO/pulls/$PR_NUMBER/reviews")" \
  || fail "gh api pulls/$PR_NUMBER/reviews failed"
reviewers_display=''
while IFS= read -r required_reviewer; do
  [[ -n "$required_reviewer" ]] || continue
  latest_review="$(jq --arg reviewer "$required_reviewer" \
    '[.[] | select(.user.login == $reviewer)] | if length == 0 then null else last end' \
    <<<"$reviews_json")"
  [[ "$latest_review" != 'null' ]] \
    || fail "required reviewer '$required_reviewer' has not reviewed PR #$PR_NUMBER"
  latest_state="$(jq -r '.state' <<<"$latest_review")"
  latest_commit="$(jq -r '.commit_id' <<<"$latest_review")"
  [[ "$latest_state" == 'APPROVED' ]] \
    || fail "required reviewer '$required_reviewer' latest review state is $latest_state (need APPROVED); a later CHANGES_REQUESTED supersedes an earlier APPROVED"
  [[ "$latest_commit" == "$head_oid" ]] \
    || fail "required reviewer '$required_reviewer' latest APPROVED is on commit ${latest_commit:0:7}, not current head ${head_oid:0:7}; re-request review on the amended head"
  reviewers_display="${reviewers_display}${reviewers_display:+, }${required_reviewer}"
done <<<"$required_reviewers"

# Belt-and-braces: aggregate reviewDecision must also reflect APPROVED.
[[ "$review_decision" == 'APPROVED' ]] \
  || fail "PR aggregate reviewDecision=$review_decision (need APPROVED); the wrapper requires both paired-agent APPROVED-on-head and aggregate APPROVED"

# 7. Explicit CI check via check-runs API (not inferred from mergeStateStatus).
check_runs_json="$(gh api "/repos/$REPO/commits/$head_oid/check-runs?per_page=100" --jq '.check_runs')" \
  || fail "gh api commits/$head_oid/check-runs failed"

total_checks="$(jq 'length' <<<"$check_runs_json")"
[[ "$total_checks" -gt 0 ]] \
  || fail "no check-runs on head $head_oid; refusing to merge without CI evidence"

bad_check_summary="$(jq -r '
  [.[] | select(
    .status != "completed"
    or (.conclusion != "success" and .conclusion != "neutral" and .conclusion != "skipped")
  )]
  | map("  \(.name): status=\(.status) conclusion=\(.conclusion // "null")")
  | .[]
' <<<"$check_runs_json")"

if [[ -n "$bad_check_summary" ]]; then
  printf 'merge-pr.sh: CI not green on head %s:\n%s\n' "$head_oid" "$bad_check_summary" >&2
  exit 1
fi

# 7b. mergeStateStatus must also be CLEAN. Explicit check-runs prove CI passed
#     on the head commit, but CLEAN additionally catches BEHIND (branch behind
#     main) — a head race that explicit CI-run inspection alone does not
#     detect, and that matters until branch protection is enabled.
[[ "$merge_state" == 'CLEAN' ]] \
  || fail "mergeStateStatus=$merge_state (need CLEAN); CI passed but the branch may be BEHIND main or otherwise unmergeable"

# 8. Merge with all mandatory flags. Lifecycle automation produces exactly
#    one bot-authored commit; verify that source author and rebase it so the
#    noreply identity is preserved without relying on squash-author profile
#    metadata. Agent-authored PRs keep the established squash path.
merge_mode='squash'
if [[ "$pr_author" == 'github-actions[bot]' ]]; then
  [[ "$commit_count" -eq 1 ]] \
    || fail "automation PR has $commit_count commits (need exactly 1 for verified rebase merge)"
  source_email="$(gh api "/repos/$REPO/commits/$head_oid" --jq '.commit.author.email')" \
    || fail "cannot verify automation head commit author for $head_oid"
  [[ "$source_email" == "$expected_email" ]] \
    || fail "automation head author email mismatch: expected $expected_email, got $source_email"
  merge_mode='rebase'
fi

printf 'merging PR #%s\n  author:    %s\n  head:      %s\n  reviewers: %s\n  mode:      %s\n  email:     %s\n' \
  "$PR_NUMBER" "$pr_author" "$head_oid" "$reviewers_display" "$merge_mode" "$expected_email"
if [[ "$merge_mode" == 'rebase' ]]; then
  gh pr merge "$PR_NUMBER" --repo "$REPO" \
    --rebase \
    --delete-branch \
    --match-head-commit "$head_oid" \
    || fail 'gh pr rebase merge failed'
else
  gh pr merge "$PR_NUMBER" --repo "$REPO" \
    --squash \
    --delete-branch \
    --match-head-commit "$head_oid" \
    --author-email "$expected_email" \
    || fail 'gh pr squash merge failed'
fi

# 9. Post-merge verification (bounded polling for API propagation).
# Wait for .merged == true before trusting merge_commit_sha: GitHub's
# merge_commit_sha field is populated PRE-merge with a synthetic test-merge
# commit and only changes to the actual squash commit after the merge
# completes. Reading it before merged=true can produce a stale value.
merge_commit=""
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  merge_commit="$(gh api "/repos/$REPO/pulls/$PR_NUMBER" \
    --jq 'select(.merged == true) | .merge_commit_sha' 2>/dev/null || true)"
  if [[ -n "$merge_commit" && "$merge_commit" != 'null' ]]; then
    break
  fi
  sleep 2
done
[[ -n "$merge_commit" && "$merge_commit" != 'null' ]] \
  || fail 'post-merge: PR did not reach merged=true after polling'

actual_email="$(poll_nonempty \
  gh api "/repos/$REPO/commits/$merge_commit" --jq '.commit.author.email')" \
  || fail "post-merge: fetching commit metadata for $merge_commit failed after polling"

if [[ "$actual_email" != "$expected_email" ]]; then
  printf 'POST-MERGE FAILURE: author email mismatch on %s\n  expected: %s\n  actual:   %s\n' \
    "$merge_commit" "$expected_email" "$actual_email" >&2
  exit 1
fi

printf 'OK: merged PR #%s as %s (author email: %s)\n' \
  "$PR_NUMBER" "$merge_commit" "$actual_email"
