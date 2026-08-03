"""Contracts for the Dependabot version-update configuration.

These tests guard the *configuration* half of Dependabot only. Alerts and
automated security fixes are repository API state, are not expressible in this
repository, and are deliberately not asserted here — see
docs/runbooks/dependabot.md. What is asserted:

  * every configured ecosystem/directory pair corresponds to a manifest that
    actually exists in the tree, so the config cannot describe a directory that
    was renamed or a portal that was never added;
  * every Terraform root make validate iterates over has an entry, so a new
    root cannot silently miss dependency currency;
  * the conservative posture (weekly, low PR limits, majors ungrouped) holds;
  * labels referenced by the config are documented in the runbook, since
    Dependabot silently drops labels that do not exist in the repository.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "dependabot.yml"
RUNBOOK = ROOT / "docs" / "runbooks" / "dependabot.md"

# Maximum open version-update PRs per ecosystem. Deliberately low: dependency
# PRs compete for the same human review attention as platform work. This is the
# declared 2-3 policy, so the bound is 3 — not Dependabot's default of 5, which
# would let an edit back to the default pass a test named "..._are_low".
MAX_OPEN_PR_LIMIT = 3

# The exact limit each block is expected to declare. Asserting the policy rather
# than a ceiling means raising any single limit is a visible, deliberate edit
# here as well as in the config.
EXPECTED_OPEN_PR_LIMITS = {
    ("github-actions", "/"): 3,
    ("terraform", "/terraform/bootstrap"): 2,
    ("terraform", "/terraform/environments/dev"): 2,
    ("terraform", "/terraform/environments/dns"): 2,
    ("pip", "/"): 2,
    ("npm", "/control-plane/portal"): 2,
}


def _load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _resolve(directory: str) -> Path:
    """Resolve a Dependabot repo-root-relative directory to a filesystem path."""
    return ROOT / directory.lstrip("/")


def _terraform_roots() -> set[str]:
    """Directories containing first-party Terraform sources, as Dependabot directories.

    `terraform init` vendors every upstream module — including their examples,
    tests, and wrappers — under `<root>/.terraform/modules/`. Those are hundreds
    of `.tf` files that are not roots, are gitignored, and exist only on a
    workstation that has run `make validate`. Globbing them would make this test
    pass in CI and fail locally, which is the worst of both. Only tracked,
    non-vendored directories count.
    """
    roots = set()
    for source in ROOT.glob("terraform/**/*.tf"):
        relative = source.parent.relative_to(ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        roots.add("/" + relative.as_posix())
    return roots


class DependabotConfigStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _load_config()
        self.updates = self.config["updates"]

    def test_uses_version_two_schema(self) -> None:
        self.assertEqual(self.config["version"], 2)

    def test_every_entry_is_unique_by_ecosystem_and_directory(self) -> None:
        pairs = [
            (entry["package-ecosystem"], entry["directory"]) for entry in self.updates
        ]
        self.assertEqual(
            len(pairs),
            len(set(pairs)),
            "duplicate ecosystem/directory pair; Dependabot would run it twice",
        )

    def test_every_configured_directory_exists(self) -> None:
        for entry in self.updates:
            with self.subTest(directory=entry["directory"]):
                self.assertTrue(
                    _resolve(entry["directory"]).is_dir(),
                    f"{entry['directory']} is configured but absent from the tree",
                )


class DependabotMatchesTheTree(unittest.TestCase):
    """Each block must be backed by a manifest the ecosystem actually reads."""

    def setUp(self) -> None:
        self.updates = _load_config()["updates"]

    def test_github_actions_blocks_have_workflows(self) -> None:
        for entry in self.updates:
            if entry["package-ecosystem"] != "github-actions":
                continue
            workflows = _resolve(entry["directory"]) / ".github" / "workflows"
            with self.subTest(directory=entry["directory"]):
                self.assertTrue(
                    any(workflows.glob("*.y*ml")),
                    f"no workflows under {workflows}",
                )

    def test_terraform_blocks_have_terraform_sources(self) -> None:
        for entry in self.updates:
            if entry["package-ecosystem"] != "terraform":
                continue
            root = _resolve(entry["directory"])
            with self.subTest(directory=entry["directory"]):
                self.assertTrue(
                    any(root.glob("*.tf")),
                    f"no .tf files in {root}",
                )

    def test_pip_blocks_have_a_requirements_file(self) -> None:
        for entry in self.updates:
            if entry["package-ecosystem"] != "pip":
                continue
            directory = _resolve(entry["directory"])
            with self.subTest(directory=entry["directory"]):
                self.assertTrue(
                    any(directory.glob("requirements*.txt"))
                    or (directory / "pyproject.toml").exists(),
                    f"no Python manifest in {directory}",
                )

    def test_npm_and_docker_blocks_require_a_real_manifest(self) -> None:
        """Guards against configuring a portal or image build that does not exist."""
        for entry in self.updates:
            ecosystem = entry["package-ecosystem"]
            if ecosystem not in {"npm", "docker"}:
                continue
            directory = _resolve(entry["directory"])
            with self.subTest(ecosystem=ecosystem, directory=entry["directory"]):
                if ecosystem == "npm":
                    self.assertTrue(
                        (directory / "package.json").exists(),
                        f"npm configured but no package.json in {directory}",
                    )
                else:
                    self.assertTrue(
                        any(directory.glob("Dockerfile*")),
                        f"docker configured but no Dockerfile in {directory}",
                    )

    def test_every_terraform_root_is_covered(self) -> None:
        """A new Terraform root must not silently miss dependency currency."""
        configured = {
            entry["directory"]
            for entry in self.updates
            if entry["package-ecosystem"] == "terraform"
        }
        self.assertEqual(
            _terraform_roots() - configured,
            set(),
            "Terraform root(s) present in the tree with no Dependabot entry",
        )

    def test_workflows_are_covered(self) -> None:
        """Actions are SHA-pinned; unattended, a pin just gets old."""
        if not any((ROOT / ".github" / "workflows").glob("*.y*ml")):
            self.skipTest("no workflows in the tree")
        self.assertIn(
            ("github-actions", "/"),
            {(entry["package-ecosystem"], entry["directory"]) for entry in self.updates},
        )

    def test_root_python_requirements_are_covered(self) -> None:
        """These pins decide what desired-state validation accepts."""
        if not any(ROOT.glob("requirements*.txt")):
            self.skipTest("no root requirements file")
        self.assertIn(
            ("pip", "/"),
            {(entry["package-ecosystem"], entry["directory"]) for entry in self.updates},
        )

    def test_first_party_npm_manifests_are_covered(self) -> None:
        """A new checked-in JavaScript application must receive update PRs."""
        ignored_parts = {"node_modules", "dist", ".next", ".vinext"}
        roots = {
            "/" + manifest.parent.relative_to(ROOT).as_posix()
            for manifest in ROOT.glob("**/package.json")
            if not ignored_parts.intersection(manifest.relative_to(ROOT).parts)
            and not any(part.startswith(".") for part in manifest.relative_to(ROOT).parts)
        }
        configured = {
            entry["directory"]
            for entry in self.updates
            if entry["package-ecosystem"] == "npm"
        }
        self.assertEqual(
            roots - configured,
            set(),
            "first-party npm manifest(s) present with no Dependabot entry",
        )


class DependabotPosture(unittest.TestCase):
    """The conservative settings this repository chose, held in place."""

    def setUp(self) -> None:
        self.updates = _load_config()["updates"]

    def test_cadence_is_weekly(self) -> None:
        for entry in self.updates:
            with self.subTest(ecosystem=entry["package-ecosystem"]):
                self.assertEqual(entry["schedule"]["interval"], "weekly")

    def test_open_pull_request_limits_are_declared_and_low(self) -> None:
        for entry in self.updates:
            with self.subTest(ecosystem=entry["package-ecosystem"]):
                limit = entry.get("open-pull-requests-limit")
                self.assertIsNotNone(
                    limit,
                    "no open-pull-requests-limit; Dependabot would default to 5",
                )
                self.assertLessEqual(
                    limit,
                    MAX_OPEN_PR_LIMIT,
                    f"limit {limit} exceeds the declared 2-3 policy "
                    f"(max {MAX_OPEN_PR_LIMIT})",
                )
                self.assertGreater(limit, 0)

    def test_each_block_declares_its_expected_open_pr_limit(self) -> None:
        """The 2-3 policy the config and runbook both claim, held exactly."""
        observed = {
            (entry["package-ecosystem"], entry["directory"]): entry.get(
                "open-pull-requests-limit"
            )
            for entry in self.updates
        }
        self.assertEqual(
            observed,
            EXPECTED_OPEN_PR_LIMITS,
            "open-PR limits drifted from the declared policy; update both this "
            "expectation and docs/runbooks/dependabot.md deliberately",
        )

    def test_commit_messages_use_the_repository_prefix_convention(self) -> None:
        for entry in self.updates:
            with self.subTest(ecosystem=entry["package-ecosystem"]):
                commit_message = entry.get("commit-message", {})
                self.assertEqual(commit_message.get("prefix"), "chore")
                self.assertEqual(commit_message.get("include"), "scope")

    def test_groups_never_batch_major_updates(self) -> None:
        """Majors arrive alone so each gets its own review."""
        for entry in self.updates:
            for name, group in entry.get("groups", {}).items():
                with self.subTest(group=name):
                    update_types = group.get("update-types")
                    self.assertIsNotNone(
                        update_types,
                        f"group {name} omits update-types, so it would batch majors",
                    )
                    self.assertNotIn("major", update_types)

    def test_groups_apply_to_version_updates(self) -> None:
        for entry in self.updates:
            for name, group in entry.get("groups", {}).items():
                with self.subTest(group=name):
                    self.assertEqual(group.get("applies-to"), "version-updates")


class DependabotRunbook(unittest.TestCase):
    def setUp(self) -> None:
        self.runbook = RUNBOOK.read_text(encoding="utf-8")
        self.updates = _load_config()["updates"]

    def test_runbook_exists_and_is_referenced_by_the_config(self) -> None:
        self.assertIn("docs/runbooks/dependabot.md", CONFIG.read_text(encoding="utf-8"))

    def test_runbook_separates_configuration_from_repository_api_state(self) -> None:
        for expected in (
            ".github/dependabot.yml",
            "Repository API state",
            "/vulnerability-alerts",
            "/automated-security-fixes",
            "/dependabot/alerts",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.runbook)

    def test_runbook_records_that_the_canonical_endpoints_are_admin_only(self) -> None:
        """A Write-level agent must not read 404 as an outage."""
        self.assertRegex(self.runbook, r"admin-only")
        self.assertIn("403", self.runbook)
        self.assertIn("204", self.runbook)

    def test_runbook_documents_creation_of_every_label_the_config_uses(self) -> None:
        documented = set(re.findall(r"gh label create (\S+)", self.runbook))
        configured = {
            label for entry in self.updates for label in entry.get("labels", [])
        }
        self.assertEqual(
            configured - documented,
            set(),
            "config references label(s) the runbook never creates; "
            "Dependabot drops labels that do not exist",
        )

    def test_runbook_notes_the_merge_wrapper_refuses_dependabot(self) -> None:
        self.assertIn("dependabot[bot]", self.runbook)
        self.assertIn("merge-pr.sh", self.runbook)

    def test_runbook_routes_dependabot_merges_through_exact_head_cli_rebase(self) -> None:
        """Not the web UI: its squash path picks an author from profile metadata.

        Rebase replays the bot-authored commit as-is, which is the same mode the
        wrapper already uses for github-actions[bot].
        """
        for expected in ("gh pr merge", "--rebase", "--match-head-commit"):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.runbook)
        self.assertNotIn(
            "through the GitHub UI",
            self.runbook,
            "runbook routes bot merges to the web UI; use the exact-head CLI "
            "rebase path so bot authorship survives the merge",
        )

    def test_runbook_states_the_explicit_alert_read_permission(self) -> None:
        """Collaborator role and token permission are separate axes.

        A Write-scoped credential is not thereby entitled to the security API.
        """
        for expected in ("Dependabot alerts: read", "security_events"):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.runbook)

    def test_no_credentials_or_personal_addresses_are_embedded(self) -> None:
        for document in (self.runbook, CONFIG.read_text(encoding="utf-8")):
            self.assertNotRegex(document, r"gh[pousr]_[A-Za-z0-9]{16,}")
            self.assertNotRegex(document, r"github_pat_[A-Za-z0-9_]{20,}")
            self.assertNotIn("@gmail.com", document)


if __name__ == "__main__":
    unittest.main()
