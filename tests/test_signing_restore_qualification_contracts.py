"""Contracts binding the signing-restore qualification design together.

The design lives in three places that must not drift apart: the qualification
contract YAML, the runbook, and the PRD invariants they cite. These checks are
static; nothing here touches AWS, the cluster, or a database.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "hack" / "qualification" / "signing-restore-qualification.yaml"
RUNBOOK_PATH = ROOT / "docs" / "runbooks" / "signing-restore-after-cold-standby.md"
ASSIGNMENTS_DIR = ROOT / "applications" / "validators" / "assignments"


class SigningRestoreQualificationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    def test_contract_authorizes_nothing_by_itself(self) -> None:
        status = self.contract["status"]
        self.assertFalse(status["executed"])
        self.assertFalse(status["signing_enablement_authorized"])
        self.assertIsNone(status["evidence_record"])

    def test_exactly_one_human_gate(self) -> None:
        """Interaction is minimized to one gate, never to zero."""
        gate = self.contract["human_gate"]
        self.assertEqual(gate["count"], 1)
        self.assertEqual(gate["id"], "human-go-no-go")
        requires = " ".join(gate["requires"])
        self.assertIn("must not be the same person", requires)

    def test_qualification_is_read_only(self) -> None:
        qualification = self.contract["post_restore_qualification"]
        self.assertFalse(qualification["mutates_aws"])
        self.assertFalse(qualification["mutates_cluster"])
        self.assertFalse(qualification["reads_secret_values"])

    def test_fingerprint_survives_cold_storage_without_secrets(self) -> None:
        fingerprint = self.contract["pre_teardown_fingerprint"]
        self.assertTrue(fingerprint["survives_cold_storage"])
        self.assertFalse(fingerprint["contains_secret_values"])
        self.assertIn("slashing_schema_version", fingerprint["fields"])
        self.assertIn("final_snapshot_identifier", fingerprint["fields"])

    def test_qualification_checks_cover_the_named_invariants(self) -> None:
        check_ids = {c["id"] for c in self.contract["post_restore_qualification"]["checks"]}
        self.assertLessEqual(
            {
                "restored-endpoint-reachable",
                "schema-compatibility",
                "row-continuity",
                "single-slashing-authority",
                "signer-prerequisites-ready",
                "assignments-still-stopped",
            },
            check_ids,
        )

    def test_enablement_travels_through_git(self) -> None:
        self.assertEqual(self.contract["enablement"]["mechanism"], "git-merge")

    def test_runbook_and_contract_reference_each_other(self) -> None:
        self.assertEqual(
            self.contract["metadata"]["runbook"],
            "docs/runbooks/signing-restore-after-cold-standby.md",
        )
        self.assertIn("signing-restore-qualification.yaml", self.runbook)

    def test_runbook_names_the_single_gate_and_fail_closed_frame(self) -> None:
        self.assertIn("exactly one human go/no-go gate", self.runbook)
        self.assertIn("human-go-no-go", self.runbook)
        self.assertIn("fail", self.runbook.lower())
        for section in ("§5.7", "§5.9", "§5.12"):
            self.assertIn(section, self.runbook)

    def test_every_assignment_is_currently_stopped(self) -> None:
        """The design's precondition holds in the repository right now."""
        assignment_files = sorted(ASSIGNMENTS_DIR.glob("*.yaml"))
        self.assertTrue(assignment_files)
        for path in assignment_files:
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = manifest.get("spec", {})
            self.assertEqual(
                spec.get("lifecycle"),
                "stopped",
                f"{path.name} is not stopped; signing-restore design "
                "precondition does not hold",
            )
            self.assertIs(
                spec.get("signingEnabled"),
                False,
                f"{path.name} has signing enabled",
            )


if __name__ == "__main__":
    unittest.main()
