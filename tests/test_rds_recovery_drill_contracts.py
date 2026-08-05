"""Contracts for the non-mutating RDS slashing-recovery drill design."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import verify_rds_recovery_drill_preflight as preflight


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "hack" / "qualification" / "rds-slashing-recovery-drill.yaml"
RUNBOOK_PATH = ROOT / "docs" / "runbooks" / "rds-slashing-recovery-drill.md"
TOOL_PATH = ROOT / "tools" / "verify_rds_recovery_drill_preflight.py"


def failed(results: list[preflight.CheckResult]) -> set[str]:
    return {result.check_id for result in results if not result.passed}


class DrillContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = preflight.load_contract(CONTRACT_PATH)

    def mutated(self) -> dict:
        return copy.deepcopy(self.contract)

    def test_checked_in_contract_passes_every_check(self) -> None:
        self.assertEqual(failed(preflight.run_checks(self.contract, ROOT)), set())

    def test_contract_records_no_execution_and_no_authorization(self) -> None:
        status = self.contract["status"]

        self.assertIs(status["executed"], False)
        self.assertIs(status["mutating_actions_authorized"], False)
        self.assertIsNone(status["evidence_record"])

    def test_restoring_in_place_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["identifier_template"] = "{source_identifier}"

        self.assertIn(
            "targets/restore-is-a-separate-instance",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_mutating_the_source_is_rejected(self) -> None:
        contract = self.mutated()
        contract["source"]["mutation_allowed"] = True

        self.assertIn(
            "targets/source-is-read-only", failed(preflight.run_checks(contract, ROOT))
        )

    def test_restore_reachable_from_the_live_signer_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["network"]["ingress_from_live_signer_group"] = "allowed"

        self.assertIn(
            "targets/restore-stays-private", failed(preflight.run_checks(contract, ROOT))
        )

    def test_unbounded_restore_lifetime_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["lifecycle"]["max_lifetime_hours"] = 720

        self.assertIn(
            "targets/restore-is-time-bounded",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_restoring_before_signing_is_disabled_is_rejected(self) -> None:
        contract = self.mutated()
        gates = contract["gates"]
        signing_index = next(
            index for index, gate in enumerate(gates) if gate["id"] == "signing-disabled"
        )
        restore_index = next(
            index
            for index, gate in enumerate(gates)
            if gate["id"] == "restore-isolated-target"
        )
        gates[signing_index], gates[restore_index] = (
            gates[restore_index],
            gates[signing_index],
        )

        self.assertIn(
            "gates/required-order", failed(preflight.run_checks(contract, ROOT))
        )

    def test_a_signing_off_gate_that_mutates_aws_is_rejected(self) -> None:
        contract = self.mutated()
        signing = next(
            gate for gate in contract["gates"] if gate["id"] == "signing-disabled"
        )
        signing["mutating"] = True

        self.assertEqual(
            failed(preflight.run_checks(contract, ROOT)),
            {
                "gates/nothing-billable-before-approval",
                "gates/signing-off-before-restore",
            },
        )

    def test_billing_before_the_human_gate_is_rejected(self) -> None:
        contract = self.mutated()
        fingerprint = next(
            gate for gate in contract["gates"] if gate["id"] == "source-fingerprint"
        )
        fingerprint["incurs_aws_cost"] = True

        self.assertIn(
            "gates/nothing-billable-before-approval",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_dropping_the_human_approval_gate_is_rejected(self) -> None:
        contract = self.mutated()
        for gate in contract["gates"]:
            gate["requires_human_approval"] = False

        self.assertIn(
            "gates/single-human-approval", failed(preflight.run_checks(contract, ROOT))
        )

    def test_a_second_approval_gate_is_rejected(self) -> None:
        contract = self.mutated()
        cleanup = next(gate for gate in contract["gates"] if gate["id"] == "cleanup")
        cleanup["requires_human_approval"] = True

        self.assertIn(
            "gates/single-human-approval", failed(preflight.run_checks(contract, ROOT))
        )

    def test_a_gate_that_understates_its_effect_is_rejected(self) -> None:
        contract = self.mutated()
        restore = next(
            gate for gate in contract["gates"] if gate["id"] == "restore-isolated-target"
        )
        restore["mutating"] = False

        self.assertIn(
            "gates/restore-is-declared-mutating",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_signing_with_a_fleet_key_is_rejected(self) -> None:
        contract = self.mutated()
        contract["rejection_test"]["live_fleet_key_use"] = "permitted"

        self.assertIn(
            "rejection/no-fleet-key", failed(preflight.run_checks(contract, ROOT))
        )

    def test_a_drill_signer_with_a_publication_path_is_rejected(self) -> None:
        contract = self.mutated()
        contract["rejection_test"]["signer"]["beacon_connection"] = "permitted"

        self.assertIn(
            "rejection/no-live-signing-path",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_rejection_test_that_expects_a_signature_is_rejected(self) -> None:
        contract = self.mutated()
        contract["rejection_test"]["expected_result"]["second_request_http_status"] = 200

        self.assertIn(
            "rejection/expects-a-refusal", failed(preflight.run_checks(contract, ROOT))
        )

    def test_claiming_execution_without_evidence_is_rejected(self) -> None:
        contract = self.mutated()
        contract["status"]["mutating_actions_authorized"] = True

        self.assertIn(
            "status/nothing-executed-or-authorized",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_an_unbounded_cost_estimate_is_rejected(self) -> None:
        contract = self.mutated()
        contract["cost"]["estimated_total_usd_max"] = 5000

        self.assertIn(
            "cost/bounded-estimate", failed(preflight.run_checks(contract, ROOT))
        )

    def test_a_missing_runbook_is_rejected(self) -> None:
        contract = self.mutated()
        contract["metadata"]["runbook"] = "docs/runbooks/does-not-exist.md"

        self.assertIn("runbook/present", failed(preflight.run_checks(contract, ROOT)))

    def test_an_undocumented_gate_is_rejected(self) -> None:
        contract = self.mutated()
        contract["gates"][0]["id"] = "signing-disabled-but-undocumented"

        self.assertIn(
            "gates/required-order", failed(preflight.run_checks(contract, ROOT))
        )

    def test_a_contract_without_gates_is_a_hard_error(self) -> None:
        contract = self.mutated()
        contract["gates"] = []

        with self.assertRaises(preflight.DrillContractError):
            preflight.run_checks(contract, ROOT)


class TerraformGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = preflight.load_contract(CONTRACT_PATH)
        self.terraform_root = ROOT / self.contract["source"]["terraform_root"]

    def test_live_terraform_declares_every_required_guard(self) -> None:
        results = preflight.check_terraform_guards(self.contract, self.terraform_root)

        self.assertNotEqual(results, [])
        self.assertEqual(failed(results), set())

    def test_every_guard_is_named_and_justified(self) -> None:
        guards = self.contract["required_terraform_guards"]
        identifiers = [guard["id"] for guard in guards]

        self.assertEqual(len(identifiers), len(set(identifiers)))
        for guard in guards:
            with self.subTest(guard=guard["id"]):
                self.assertTrue(guard["rationale"].strip())

    def test_a_removed_recovery_guard_fails_the_contract(self) -> None:
        original = (self.terraform_root / "signer-foundation.tf").read_text(
            encoding="utf-8"
        )
        weakened = original.replace(
            "delete_automated_backups  = false",
            "delete_automated_backups  = true",
        )
        self.assertNotEqual(original, weakened)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "signer-foundation.tf").write_text(weakened, encoding="utf-8")
            (root / "variables.tf").write_text(
                (self.terraform_root / "variables.tf").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            results = preflight.check_terraform_guards(self.contract, root)

        self.assertIn("terraform-guard/backups-outlive-the-instance", failed(results))

    def test_an_undeclared_terraform_block_is_a_hard_error(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["required_terraform_guards"][0]["scope"] = "resource:aws_db_instance.absent"

        with self.assertRaises(preflight.DrillContractError):
            preflight.check_terraform_guards(contract, self.terraform_root)


class VerificationQueryTests(unittest.TestCase):
    FORBIDDEN = ("public_key", "signing_root", "validator_id")

    def check(self, sql: str) -> preflight.CheckResult:
        return preflight.check_query_is_aggregate_only("probe", sql, self.FORBIDDEN)

    def test_aggregate_query_is_accepted(self) -> None:
        self.assertTrue(
            self.check("SELECT count(*) AS validator_rows FROM validators").passed
        )

    def test_write_statement_is_rejected(self) -> None:
        result = self.check("DELETE FROM signed_blocks")

        self.assertFalse(result.passed)
        self.assertIn("read-only", result.detail)

    def test_write_hidden_after_a_select_is_rejected(self) -> None:
        self.assertFalse(
            self.check(
                "SELECT count(*) AS rows FROM validators; DROP TABLE validators"
            ).passed
        )

    def test_select_into_is_rejected(self) -> None:
        self.assertFalse(
            self.check("SELECT count(*) AS rows INTO copy FROM validators").passed
        )

    def test_raw_column_projection_is_rejected(self) -> None:
        result = self.check("SELECT public_key AS key FROM validators")

        self.assertFalse(result.passed)
        self.assertIn("aggregate", result.detail)

    def test_forbidden_alias_is_rejected(self) -> None:
        result = self.check("SELECT max(id) AS validator_id FROM validators")

        self.assertFalse(result.passed)
        self.assertIn("forbidden", result.detail)

    def test_unaliased_projection_is_rejected(self) -> None:
        self.assertFalse(self.check("SELECT count(*) FROM validators").passed)

    def test_nested_aggregate_over_a_hashed_column_is_accepted(self) -> None:
        sql = (
            "SELECT count(*) AS covered_rows, "
            "md5(string_agg(row_digest, ',' ORDER BY row_digest)) AS digest "
            "FROM (SELECT encode(hmac(v.public_key::text, :salt, 'sha256'), 'hex') "
            "AS row_digest FROM validators v) AS covered"
        )

        self.assertTrue(self.check(sql).passed)


class RedactionTests(unittest.TestCase):
    def test_every_disclosure_class_is_detected(self) -> None:
        samples = {
            "aws-account-id": "account 012345678901 owns it",
            "aws-arn": "arn:aws:rds:region:acct:db:name",
            "rds-endpoint-hostname": "host db-one.abc.us-west-2.rds.amazonaws.com",
            "jdbc-connection-string": "jdbc:postgresql://host/db",
            "ip-address": "peer at 198.51.100.7",
            "bls-public-key": "key 0x" + "ab" * 48,
            "secret-value": "password: hunter2",
        }
        for expected, sample in samples.items():
            with self.subTest(disclosure=expected):
                self.assertIn(expected, preflight.scan_for_disclosure(sample))

    def test_clean_text_reports_nothing(self) -> None:
        self.assertEqual(
            preflight.scan_for_disclosure(
                "The restored copy passed schema and continuity checks."
            ),
            [],
        )

    def test_readiness_output_refuses_to_carry_a_disclosure(self) -> None:
        contract = preflight.load_contract(CONTRACT_PATH)
        # A guard expectation is echoed into the report detail, so it is the
        # shortest path from a contract edit to a leaked identifier.
        contract["required_terraform_guards"][0]["expect"] = "arn:aws:rds:us-west-2:"

        with self.assertRaises(preflight.DrillContractError) as raised:
            preflight.build_report(contract, ROOT, "markdown")

        self.assertIn("aws-arn", str(raised.exception))

    def test_committed_drill_documents_carry_no_disclosure(self) -> None:
        for path in (CONTRACT_PATH, RUNBOOK_PATH):
            with self.subTest(path=path.name):
                self.assertEqual(
                    preflight.scan_for_disclosure(path.read_text(encoding="utf-8")), []
                )


class PreflightCommandTests(unittest.TestCase):
    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOL_PATH), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_markdown_readiness_is_clean_and_passes(self) -> None:
        completed = self.run_tool()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("It is not recovery evidence.", completed.stdout)
        self.assertNotIn("FAIL", completed.stdout)
        self.assertEqual(preflight.scan_for_disclosure(completed.stdout), [])

    def test_json_readiness_states_nothing_was_executed(self) -> None:
        completed = self.run_tool("--format", "json")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIs(payload["passed"], True)
        self.assertIs(payload["executed"], False)
        self.assertIs(payload["mutating_actions_authorized"], False)
        self.assertTrue(all(check["passed"] for check in payload["checks"]))

    def test_a_broken_contract_exits_non_zero_without_printing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.yaml"
            path.write_text("apiVersion: wrong\nkind: wrong\n", encoding="utf-8")
            completed = self.run_tool("--contract", str(path))

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertIn("drill preflight failed", completed.stderr)


class DrillSourceCodeTests(unittest.TestCase):
    def test_the_preflight_reaches_no_cloud_or_database(self) -> None:
        source = TOOL_PATH.read_text(encoding="utf-8")

        for forbidden in ("import boto3", "import psycopg", "urllib", "socket", "subprocess"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_runbook_states_nothing_has_been_executed(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        self.assertIn("the drill has never been run", runbook)
        self.assertIn("Live signing is unchanged", runbook)
        self.assertIn("It is never part of the drill session", runbook)


if __name__ == "__main__":
    unittest.main()
