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

    def test_every_compared_field_has_defined_semantics(self) -> None:
        """No field is compared without an ordering or an equality rule.

        Digests carry no ordering; only integer maxima and counts may use
        the >= comparison.
        """
        rule = self.contract["continuity_rule"]
        fields = set(self.contract["pre_teardown_fingerprint"]["fields"])
        ordered = set(rule["ordered_fields"])
        equality_only = set(rule["equality_only_fields"])
        self.assertEqual(ordered & equality_only, set())
        self.assertLessEqual(ordered | equality_only, fields)
        self.assertEqual(rule["ordered_comparison"], "restored_must_be_greater_or_equal")
        self.assertIn("digest", " ".join(equality_only))
        self.assertNotIn("digest", " ".join(ordered))
        self.assertEqual(rule["on_any_violation"], "fail-closed")

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

    def test_continuity_rule_semantics_on_reference_cases(self) -> None:
        """Execute the contract's comparison rule against known cases.

        The reference evaluator is the rule as written: ordered fields pass
        when restored >= captured, equality-only fields pass on exact match,
        and any violation fails.
        """
        rule = self.contract["continuity_rule"]

        def evaluate(captured: dict, restored: dict) -> bool:
            for field in rule["ordered_fields"]:
                if field in captured and restored.get(field, -1) < captured[field]:
                    return False
            for field in rule["equality_only_fields"]:
                if field in captured and restored.get(field) != captured[field]:
                    return False
            return True

        captured = {
            "validator_identity_count": 3,
            "per_validator_max_signed_block_slot": 4_100_200,
            "per_validator_max_signed_attestation_source_epoch": 128_130,
            "per_validator_max_signed_attestation_target_epoch": 128_131,
            "per_validator_signed_record_count": 9_412,
            "content_digest": "sha256:aa11",
        }

        intact = dict(captured)
        self.assertTrue(evaluate(captured, intact))

        extended = dict(captured, per_validator_max_signed_block_slot=4_100_500)
        extended["content_digest"] = "sha256:aa11"
        self.assertTrue(evaluate(captured, extended))

        regressed_slot = dict(captured, per_validator_max_signed_block_slot=4_099_999)
        self.assertFalse(evaluate(captured, regressed_slot))

        regressed_epoch = dict(
            captured, per_validator_max_signed_attestation_target_epoch=128_000
        )
        self.assertFalse(evaluate(captured, regressed_epoch))

        regressed_count = dict(captured, per_validator_signed_record_count=9_000)
        self.assertFalse(evaluate(captured, regressed_count))

        lost_validator = dict(captured, validator_identity_count=2)
        self.assertFalse(evaluate(captured, lost_validator))

        digest_mismatch = dict(captured, content_digest="sha256:bb22")
        self.assertFalse(
            evaluate(captured, digest_mismatch),
            "a digest mismatch with intact ordered values must still fail closed",
        )

        higher_digest_is_not_a_pass = dict(captured, content_digest="sha256:ff99")
        self.assertFalse(
            evaluate(captured, higher_digest_is_not_a_pass),
            "digests have no ordering; a 'larger' hash is just a mismatch",
        )

    def test_assignments_remain_non_signing_until_safety_is_confirmed(self) -> None:
        """Only the explicitly qualified assignment may sign."""
        qualified = {"assignment-ephemery-162-synthetic.yaml"}
        assignment_files = sorted(ASSIGNMENTS_DIR.glob("*.yaml"))
        self.assertTrue(assignment_files)
        for path in assignment_files:
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = manifest.get("spec", {})
            safety = spec.get("safety", {})
            if path.name in qualified:
                self.assertIs(spec.get("signingEnabled"), True)
                self.assertEqual(spec.get("lifecycle"), "active")
                self.assertTrue(safety.get("slashingProtectionConfirmed"))
                self.assertTrue(safety.get("doppelgangerProtectionConfirmed"))
                continue
            self.assertIs(spec.get("signingEnabled"), False, f"{path.name} has signing enabled")
            if not (
                safety.get("slashingProtectionConfirmed")
                and safety.get("doppelgangerProtectionConfirmed")
            ):
                self.assertFalse(
                    spec.get("signingEnabled"),
                    f"{path.name} enables signing without both safety confirmations",
                )


if __name__ == "__main__":
    unittest.main()
