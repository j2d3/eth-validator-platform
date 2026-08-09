"""Contracts for the post-main-push pull-request rebase workflow."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rebase-open-pull-requests.yaml"


def _on(workflow: dict) -> object:
    # PyYAML 5/6 under YAML 1.1 parses the key ``on`` as boolean True.
    return workflow.get("on", workflow.get(True))


class RebaseOpenPullRequestsWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)

    def test_runs_only_after_a_main_push(self) -> None:
        self.assertEqual(_on(self.workflow), {"push": {"branches": ["main"]}})
        self.assertNotIn("pull_request_target", self.text)
        self.assertNotIn("pull_request:", self.text)

    def test_permissions_are_limited_to_branch_update(self) -> None:
        self.assertEqual(
            self.workflow["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )

    def test_rebases_only_eligible_same_repository_prs(self) -> None:
        self.assertIn("state=open&base=main", self.text)
        self.assertIn("select(.draft != true)", self.text)
        self.assertIn("select(.head.repo != null)", self.text)
        self.assertIn("select(.head.repo.full_name == env.REPOSITORY)", self.text)
        self.assertIn('select(.user.login != "dependabot[bot]")', self.text)
        self.assertIn('select(.user.login != "github-actions[bot]")', self.text)

    def test_uses_the_graphql_rebase_mutation(self) -> None:
        self.assertIn("gh api graphql", self.text)
        self.assertIn("updatePullRequestBranch", self.text)
        self.assertIn("updateMethod: REBASE", self.text)
        self.assertIn("expectedHeadOid", self.text)
        self.assertNotIn("pulls/$number/update-branch", self.text)

    def test_conflicts_are_reported_and_never_merged(self) -> None:
        self.assertIn("could not be rebased; leaving it open", self.text)
        self.assertIn("failed=1", self.text)
        self.assertIn("exit 1", self.text)
        self.assertNotIn("gh pr merge", self.text)
        self.assertNotIn("--admin", self.text)


if __name__ == "__main__":
    unittest.main()
