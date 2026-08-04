from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml

from tools import render_local_assignments, set_node_pair_lifecycle


ROOT = Path(__file__).resolve().parents[1]


class LocalAssignmentProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = render_local_assignments.load_catalog()
        self.assignment_name = "assignment-synthetic-01"

    def release(self, catalog=None):
        return render_local_assignments.build_release(
            self.assignment_name,
            catalog or self.catalog,
        )

    def test_committed_projection_is_generated_from_the_catalog(self) -> None:
        files = render_local_assignments.rendered_files(self.catalog)
        self.assertEqual(render_local_assignments.projection_errors(files), [])

    def test_default_projection_is_stopped_and_non_signing(self) -> None:
        release = self.release()
        values = release["spec"]["values"]

        self.assertEqual(values["lifecycleState"], "stopped")
        self.assertEqual(values["fullnameOverride"], "pair-validator-synthetic-01")
        self.assertTrue(release["spec"]["install"]["disableWait"])
        self.assertTrue(release["spec"]["upgrade"]["disableWait"])
        self.assertFalse(values["validator"]["enabled"])
        self.assertFalse(values["validator"]["slashingProtectionConfirmed"])
        self.assertNotIn("publicKey", values["validator"])
        self.assertNotIn("signingSecretRef", yaml.safe_dump(release))

    def test_chart_record_feeds_the_cluster_teardown_guard(self) -> None:
        record = (
            ROOT / "charts" / "ethereum-node" / "templates" / "record.yaml"
        ).read_text(encoding="utf-8")
        guard = (ROOT / "hack" / "local-cluster.sh").read_text(encoding="utf-8")

        self.assertIn("platform.galaxy-lab/signing-enabled", record)
        self.assertIn("platform.galaxy-lab/signing-enabled=true", guard)

    def test_active_projection_launches_only_the_node_pair(self) -> None:
        catalog = copy.deepcopy(self.catalog)
        assignment = catalog["ValidatorAssignment"][self.assignment_name]
        assignment["spec"]["lifecycle"] = "active"
        assignment["spec"]["nodePairRef"] = "pair-validator-synthetic-01"

        release = self.release(catalog)
        values = release["spec"]["values"]

        self.assertEqual(values["lifecycleState"], "active")
        self.assertEqual(values["fullnameOverride"], "pair-validator-synthetic-01")
        self.assertFalse(release["spec"]["install"]["disableWait"])
        self.assertFalse(release["spec"]["upgrade"]["disableWait"])
        self.assertEqual(values["executionClient"], "geth")
        self.assertEqual(values["consensusClient"], "lighthouse")
        self.assertFalse(values["validator"]["enabled"])

    def test_intermediate_and_failed_states_fail_closed_to_stopped(self) -> None:
        for lifecycle in ("activating", "failed-safe", "stopping", "switching", "archiving"):
            with self.subTest(lifecycle=lifecycle):
                self.assertEqual(render_local_assignments.chart_lifecycle(lifecycle), "stopped")

                catalog = copy.deepcopy(self.catalog)
                catalog["ValidatorAssignment"][self.assignment_name]["spec"]["lifecycle"] = lifecycle
                release = self.release(catalog)
                self.assertTrue(release["spec"]["install"]["disableWait"])
                self.assertTrue(release["spec"]["upgrade"]["disableWait"])

    def test_projection_refuses_signing_catalog_state(self) -> None:
        catalog = copy.deepcopy(self.catalog)
        catalog["ValidatorAssignment"][self.assignment_name]["spec"]["signingEnabled"] = True

        with self.assertRaisesRegex(render_local_assignments.ProjectionError, "non-signing only"):
            self.release(catalog)

    def test_projection_refuses_unimplemented_client_adapter(self) -> None:
        # Reth is now a supported adapter (see PR-B chart change + PR-C
        # projection extension). This test still needs a client name the
        # projection tool does not know so its fail-closed path is exercised;
        # Nethermind is the next-in-line EL adapter (see the ServiceProfile
        # schema's enum) and has not been implemented.
        catalog = copy.deepcopy(self.catalog)
        profile = catalog["ServiceProfile"]["dedicated-geth-lighthouse"]
        profile["spec"]["executionClient"] = "nethermind"

        with self.assertRaisesRegex(render_local_assignments.ProjectionError, "no local adapter"):
            self.release(catalog)


class NodePairLifecycleTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = render_local_assignments.load_catalog()
        self.assignment = copy.deepcopy(
            catalog["ValidatorAssignment"]["assignment-synthetic-01"]
        )

    def test_activate_from_stopped_preserves_no_signing_contract(self) -> None:
        stopped_resource_identity = render_local_assignments.build_release(
            self.assignment["metadata"]["name"],
            render_local_assignments.load_catalog(),
        )["spec"]["values"]["fullnameOverride"]
        result = set_node_pair_lifecycle.transition_assignment(
            self.assignment,
            "activate",
            "Begin non-signing sync qualification",
        )

        self.assertEqual(result["spec"]["lifecycle"], "active")
        self.assertEqual(result["spec"]["nodePairRef"], "pair-validator-synthetic-01")
        self.assertEqual(result["spec"]["nodePairRef"], stopped_resource_identity)
        self.assertFalse(result["spec"]["signingEnabled"])
        self.assertEqual(
            result["spec"]["safety"],
            {
                "slashingProtectionConfirmed": False,
                "doppelgangerProtectionConfirmed": False,
            },
        )

    def test_stop_from_active_resets_self_attested_safety_flags(self) -> None:
        self.assignment["spec"]["lifecycle"] = "active"
        self.assignment["spec"]["safety"] = {
            "slashingProtectionConfirmed": True,
            "doppelgangerProtectionConfirmed": True,
        }

        result = set_node_pair_lifecycle.transition_assignment(
            self.assignment,
            "stop",
            "Complete this client-pair practice run",
        )

        self.assertEqual(result["spec"]["lifecycle"], "stopped")
        self.assertFalse(any(result["spec"]["safety"].values()))

    def test_invalid_or_duplicate_transition_fails(self) -> None:
        with self.assertRaisesRegex(set_node_pair_lifecycle.LifecycleError, "cannot stop"):
            set_node_pair_lifecycle.transition_assignment(
                self.assignment,
                "stop",
                "Already stopped",
            )

    def test_signed_assignment_is_outside_this_workflow(self) -> None:
        self.assignment["spec"]["signingEnabled"] = True
        with self.assertRaisesRegex(set_node_pair_lifecycle.LifecycleError, "cannot manage"):
            set_node_pair_lifecycle.transition_assignment(
                self.assignment,
                "activate",
                "Must use qualified path",
            )

    def test_reason_is_required_and_bounded(self) -> None:
        for reason in ("", "no", "x" * 257):
            with self.subTest(length=len(reason)):
                with self.assertRaisesRegex(set_node_pair_lifecycle.LifecycleError, "reason"):
                    set_node_pair_lifecycle.transition_assignment(
                        copy.deepcopy(self.assignment),
                        "activate",
                        reason,
                    )

    def test_default_pair_reference_is_stable_and_schema_bounded(self) -> None:
        validator_ref = "v" + "a" * 62
        first = render_local_assignments.default_node_pair_ref(validator_ref)
        second = render_local_assignments.default_node_pair_ref(validator_ref)

        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 63)
        self.assertRegex(first, r"^[a-z][a-z0-9-]{2,62}$")


class LifecycleWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = ROOT / ".github" / "workflows" / "node-pair-lifecycle.yaml"
        self.text = self.path.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)

    def test_workflow_is_manual_and_minimally_permissioned(self) -> None:
        trigger = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertEqual(
            self.workflow["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )

    def test_workflow_has_no_cloud_or_cluster_authority(self) -> None:
        lowered = self.text.lower()
        for forbidden in ("aws-access-key", "aws-actions/", "kubectl", "kubeconfig", "id-token: write"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_untrusted_reason_enters_shell_only_through_environment(self) -> None:
        self.assertIn("REASON: ${{ inputs.reason }}", self.text)
        self.assertNotIn('--reason "${{ inputs.reason }}"', self.text)
        self.assertIn('[[ "$ASSIGNMENT" =~ ^[a-z][a-z0-9-]{2,62}$ ]]', self.text)

    def test_workflow_invokes_non_signing_transition_and_opens_pr(self) -> None:
        self.assertIn("tools/set_node_pair_lifecycle.py", self.text)
        self.assertIn("tools/render_local_assignments.py --check", self.text)
        self.assertIn("gh pr create", self.text)
        self.assertNotIn("signingEnabled: true", self.text)


class AutomationMergeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / "hack" / "merge-pr.sh").read_text(encoding="utf-8")

    def test_lifecycle_bot_has_canonical_noreply_identity(self) -> None:
        self.assertIn(
            "41898282+github-actions[bot]@users.noreply.github.com",
            self.text,
        )

    def test_lifecycle_bot_requires_both_agents_on_exact_head(self) -> None:
        self.assertIn("'github-actions[bot]') printf 'j2d3\\n5u6r054'", self.text)
        self.assertIn('latest_commit" == "$head_oid', self.text)

    def test_lifecycle_bot_is_single_commit_rebase_not_squash(self) -> None:
        self.assertIn('[[ "$commit_count" -eq 1 ]]', self.text)
        self.assertIn("merge_mode='rebase'", self.text)
        self.assertIn("--rebase", self.text)
        self.assertIn("--match-head-commit", self.text)

    def test_unknown_pr_authors_still_fail_closed(self) -> None:
        self.assertIn("unknown PR author", self.text)
        self.assertIn("github-actions[bot]", self.text)


if __name__ == "__main__":
    unittest.main()
