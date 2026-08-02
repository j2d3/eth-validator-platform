# Contributing

All changes use pull requests, including operator-triggered client changes.

1. Create a short-lived branch.
2. Run `make check`.
3. Explain risk, rollback, test evidence, and whether validator duties could be affected.
4. Obtain review and merge. All PRs are merged via `./hack/merge-pr.sh <pr-number>`, which enforces exact-head review and noreply attribution. Agent-authored PRs require the paired agent; `github-actions[bot]` lifecycle PRs require both agents and exactly one verified bot-authored commit. Meta-tooling PRs additionally require an issue #6 notice before opening (per [COLLABORATION.md](COLLABORATION.md)). Do not apply Kubernetes application manifests from a workstation.

Infrastructure changes produce a Terraform plan in CI. Apply is a separate, reviewed GitHub Environment action. Application changes are reconciled by Flux after merge.

Client image updates require review of upstream release notes, compatibility with the selected testnet fork, rendered manifests, sync testing, and a canary rollout before broad promotion.
