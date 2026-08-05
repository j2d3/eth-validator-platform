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
        signing["mutates_aws"] = True

        self.assertEqual(
            failed(preflight.run_checks(contract, ROOT)),
            {
                "gates/nothing-billable-before-approval",
                "gates/signing-off-before-restore",
            },
        )

    def test_signing_off_may_change_cluster_state_before_the_human_gate(self) -> None:
        signing = next(
            gate for gate in self.contract["gates"] if gate["id"] == "signing-disabled"
        )

        # The distinction is the point: suspending the layers removes running
        # workloads, and the contract says so, but it creates no AWS resource.
        self.assertIs(signing["mutates_cluster_state"], True)
        self.assertIs(signing["mutates_aws"], False)
        self.assertIs(signing["incurs_aws_cost"], False)
        self.assertEqual(failed(preflight.run_checks(self.contract, ROOT)), set())

    def test_a_signing_off_gate_that_hides_its_workload_effect_is_rejected(self) -> None:
        contract = self.mutated()
        signing = next(
            gate for gate in contract["gates"] if gate["id"] == "signing-disabled"
        )
        signing["mutates_cluster_state"] = False

        self.assertIn(
            "gates/signing-off-declares-its-workload-effect",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_gate_missing_an_effect_flag_is_a_hard_error(self) -> None:
        contract = self.mutated()
        del contract["gates"][0]["mutates_cluster_state"]

        with self.assertRaises(preflight.DrillContractError):
            preflight.run_checks(contract, ROOT)

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
        restore["mutates_aws"] = False

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


class RestoreTargetTests(unittest.TestCase):
    """A point-in-time restore inherits none of the source's safety posture."""

    def setUp(self) -> None:
        self.contract = preflight.load_contract(CONTRACT_PATH)

    def mutated(self) -> dict:
        return copy.deepcopy(self.contract)

    def test_every_unsafe_aws_default_is_overridden_explicitly(self) -> None:
        declared = {
            parameter["id"]
            for parameter in self.contract["restore_target"]["explicit_restore_parameters"]
        }

        self.assertEqual(preflight.REQUIRED_RESTORE_PARAMETERS - declared, set())
        for parameter in self.contract["restore_target"]["explicit_restore_parameters"]:
            with self.subTest(parameter=parameter["id"]):
                # The stated AWS default is what the value is weighed against.
                self.assertTrue(parameter["default_if_omitted"].strip())

    def test_restore_parameters_use_runnable_cli_boolean_syntax(self) -> None:
        # `--deletion-protection false` is a parse error, not a setting. The CLI
        # spells a disabled boolean as the `--no-` form of the flag.
        for parameter in self.contract["restore_target"]["explicit_restore_parameters"]:
            with self.subTest(parameter=parameter["id"]):
                self.assertNotIn(parameter["id"], preflight.DISABLED_BY_NO_PREFIX)
                self.assertNotIn(
                    parameter["value"].strip().lower(), preflight.BOOLEAN_VALUE_TOKENS
                )

    def test_a_value_style_boolean_flag_is_rejected(self) -> None:
        contract = self.mutated()
        for parameter in contract["restore_target"]["explicit_restore_parameters"]:
            if parameter["id"] == "no-deletion-protection":
                parameter["id"] = "deletion-protection"
                parameter["value"] = "false"

        self.assertIn(
            "targets/restore-parameters-are-explicit",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_the_deletion_protection_default_is_the_documented_one(self) -> None:
        # AWS documents deletion protection as disabled by default on a restore;
        # it is not carried over from the source.
        parameter = next(
            parameter
            for parameter in self.contract["restore_target"]["explicit_restore_parameters"]
            if parameter["id"] == "no-deletion-protection"
        )
        default = " ".join(parameter["default_if_omitted"].split())

        self.assertIn("not enabled by default", default)
        self.assertIn("not copied from the source", default)

    def test_the_multi_az_default_is_the_documented_one(self) -> None:
        # The restore command description documents a new instance as single-AZ
        # by default, except for SQL Server with a mirroring option group. The
        # flag is still supplied so the reviewed call states its own posture.
        parameter = next(
            parameter
            for parameter in self.contract["restore_target"]["explicit_restore_parameters"]
            if parameter["id"] == "no-multi-az"
        )
        default = " ".join(parameter["default_if_omitted"].split())

        self.assertIn("single-AZ deployment by default", default)
        self.assertIn("SQL Server", default)

    def test_backup_retention_is_supplied_as_zero_on_the_restore_call(self) -> None:
        # The restore API does accept the parameter. Leaving it out is not
        # neutral: the documented default is the source's existing setting, so
        # the drill copy would keep its own backups of slashing history.
        parameter = next(
            parameter
            for parameter in self.contract["restore_target"]["explicit_restore_parameters"]
            if parameter["id"] == "backup-retention-period"
        )
        backup = self.contract["restore_target"]["backup"]

        self.assertIn("backup-retention-period", preflight.REQUIRED_RESTORE_PARAMETERS)
        self.assertEqual(parameter["restore_call_value"], 0)
        self.assertIs(backup["supplied_on_restore_call"], True)
        # Supplied and expected have to be the same number, or reading it back
        # afterwards compares against something nobody asked for.
        self.assertEqual(
            parameter["restore_call_value"], backup["target_backup_retention_days"]
        )

    def test_omitting_backup_retention_from_the_restore_call_is_rejected(self) -> None:
        contract = self.mutated()
        target = contract["restore_target"]
        target["explicit_restore_parameters"] = [
            parameter
            for parameter in target["explicit_restore_parameters"]
            if parameter["id"] != "backup-retention-period"
        ]

        self.assertIn(
            "targets/restore-parameters-are-explicit",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_observing_retention_instead_of_supplying_it_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["backup"]["supplied_on_restore_call"] = False

        self.assertIn(
            "targets/backup-retention-mismatch-fails-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_supplying_a_retention_the_check_does_not_expect_is_rejected(self) -> None:
        # Supplying 7 while verifying against 0 would make the post-restore read
        # a comparison against a value the approved call never contained.
        contract = self.mutated()
        for parameter in contract["restore_target"]["explicit_restore_parameters"]:
            if parameter["id"] == "backup-retention-period":
                parameter["restore_call_value"] = 7

        self.assertIn(
            "targets/backup-retention-mismatch-fails-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_an_untyped_retention_value_is_rejected(self) -> None:
        # `--backup-retention-period` is an integer option, so prose alone is not
        # a value a check can compare.
        contract = self.mutated()
        for parameter in contract["restore_target"]["explicit_restore_parameters"]:
            if parameter["id"] == "backup-retention-period":
                del parameter["restore_call_value"]

        self.assertIn(
            "targets/restore-parameters-are-explicit",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_the_cli_capability_gate_refuses_before_the_first_gate(self) -> None:
        # The parameter is in the current API but not in every installed CLI.
        # The drill refuses at preconditions rather than silently omitting it.
        gate = next(
            gate
            for gate in self.contract["restore_target"]["cli_capability_preconditions"]
            if gate["parameter"] == "backup-retention-period"
        )

        self.assertEqual(gate["must_expose"], "--backup-retention-period")
        self.assertTrue(gate["command"].endswith("help"))
        self.assertIn("restore-db-instance-to-point-in-time", gate["command"])
        self.assertEqual(gate["on_missing"], preflight.ABORT_BEFORE_FIRST_GATE)
        self.assertIs(gate["omitting_the_parameter_is_an_accepted_fallback"], False)
        self.assertTrue(gate["remediation"].strip())

    def test_dropping_the_cli_capability_gate_is_rejected(self) -> None:
        # Another gate remains, so this is a version-dependent parameter left
        # ungated rather than a structurally absent section.
        contract = self.mutated()
        contract["restore_target"]["cli_capability_preconditions"] = [
            {
                "id": "unrelated",
                "parameter": "db-subnet-group-name",
                "command": "aws rds restore-db-instance-to-point-in-time help",
                "must_expose": "--db-subnet-group-name",
                "on_missing": preflight.ABORT_BEFORE_FIRST_GATE,
                "omitting_the_parameter_is_an_accepted_fallback": False,
                "remediation": "upgrade the AWS CLI",
            }
        ]

        self.assertIn(
            "targets/cli-capability-gate-is-fail-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_contract_without_any_capability_gate_is_a_hard_error(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["cli_capability_preconditions"] = []

        with self.assertRaises(preflight.DrillContractError):
            preflight.run_checks(contract, ROOT)

    def test_accepting_an_omitted_parameter_as_a_fallback_is_rejected(self) -> None:
        contract = self.mutated()
        for gate in contract["restore_target"]["cli_capability_preconditions"]:
            gate["omitting_the_parameter_is_an_accepted_fallback"] = True

        self.assertIn(
            "targets/cli-capability-gate-is-fail-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_capability_gate_that_continues_on_a_missing_flag_is_rejected(self) -> None:
        contract = self.mutated()
        for gate in contract["restore_target"]["cli_capability_preconditions"]:
            gate["on_missing"] = "warn-and-continue"

        self.assertIn(
            "targets/cli-capability-gate-is-fail-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_checking_a_cli_version_instead_of_the_installed_help_is_rejected(self) -> None:
        # A version number is not the capability. The gap between the published
        # reference and the installed command is exactly what this gate covers,
        # so it has to interrogate the installed command's own help.
        contract = self.mutated()
        for gate in contract["restore_target"]["cli_capability_preconditions"]:
            gate["command"] = "aws --version"

        self.assertIn(
            "targets/cli-capability-gate-is-fail-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_backup_retention_mismatch_aborts_and_cleans_up(self) -> None:
        on_mismatch = self.contract["restore_target"]["backup"]["on_mismatch"]

        self.assertEqual(on_mismatch["action"], preflight.ABORT_AND_CLEANUP)
        self.assertIs(on_mismatch["modify_target"], False)
        self.assertTrue(on_mismatch["rationale"].strip())

    def test_repairing_a_backup_retention_mismatch_in_place_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["backup"]["on_mismatch"]["modify_target"] = True

        self.assertIn(
            "targets/backup-retention-mismatch-fails-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_continuing_after_a_backup_retention_mismatch_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["backup"]["on_mismatch"]["action"] = (
            "record-and-continue"
        )

        self.assertIn(
            "targets/backup-retention-mismatch-fails-closed",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_backup_plan_without_a_mismatch_response_is_a_hard_error(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["backup"]["on_mismatch"] = "abort"

        with self.assertRaises(preflight.DrillContractError):
            preflight.run_checks(contract, ROOT)

    def test_dropping_the_tls_parameter_group_is_rejected(self) -> None:
        contract = self.mutated()
        target = contract["restore_target"]
        target["explicit_restore_parameters"] = [
            parameter
            for parameter in target["explicit_restore_parameters"]
            if parameter["id"] != "db-parameter-group-name"
        ]

        self.assertIn(
            "targets/restore-parameters-are-explicit",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_dropping_the_isolated_subnet_group_is_rejected(self) -> None:
        contract = self.mutated()
        target = contract["restore_target"]
        target["explicit_restore_parameters"] = [
            parameter
            for parameter in target["explicit_restore_parameters"]
            if parameter["id"] != "db-subnet-group-name"
        ]

        self.assertIn(
            "targets/restore-parameters-are-explicit",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_restore_that_is_never_re_verified_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["post_restore_verification"] = []

        self.assertIn(
            "targets/restore-parameters-are-explicit",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_repointing_the_live_credential_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["live_external_secret_repoint"] = (
            "permitted"
        )

        self.assertIn(
            "targets/target-credentials-are-isolated",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_reading_the_target_master_credential_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["master_credential"][
            "read_by_operator"
        ] = True

        self.assertIn(
            "targets/target-credentials-are-isolated",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_drill_secret_that_outlives_the_drill_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["drill_secret"][
            "deleted_at_cleanup"
        ] = False

        self.assertIn(
            "targets/target-credentials-are-isolated",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_target_that_keeps_its_own_backups_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["backup"]["target_backup_retention_days"] = 7

        self.assertIn(
            "targets/target-backups-are-not-retained",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_deleting_the_target_without_dropping_its_backups_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["backup"]["delete_flags"] = ["skip-final-snapshot"]

        self.assertIn(
            "targets/target-backups-are-not-retained",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_target_without_a_credentials_plan_is_a_hard_error(self) -> None:
        contract = self.mutated()
        del contract["restore_target"]["credentials"]

        with self.assertRaises(preflight.DrillContractError):
            preflight.run_checks(contract, ROOT)


class DrillConnectionSplitTests(unittest.TestCase):
    """The restored host and the credential fields need disjoint owners.

    The Secrets Manager connection object carries the live host. Anything that
    projects it into the drill Secret is re-asserted by External Secrets Operator
    on every refresh, so a drill endpoint written over it would be reverted to
    the live instance without anyone doing anything.
    """

    def setUp(self) -> None:
        self.contract = preflight.load_contract(CONTRACT_PATH)
        self.credentials = self.contract["restore_target"]["credentials"]

    def mutated(self) -> dict:
        return copy.deepcopy(self.contract)

    def test_the_drill_secret_projects_credentials_only(self) -> None:
        drill_secret = self.credentials["drill_secret"]

        self.assertEqual(
            set(drill_secret["projected_keys"]), preflight.CREDENTIAL_PROJECTION
        )
        self.assertIn("host", drill_secret["excluded_keys"])
        self.assertTrue(drill_secret["exclusion_reason"].strip())

    def test_projecting_the_host_into_the_drill_secret_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["drill_secret"]["projected_keys"].append(
            "host"
        )

        self.assertIn(
            "targets/drill-connection-split-is-reconciliation-stable",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_dropping_the_host_exclusion_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["drill_secret"]["excluded_keys"] = []

        self.assertIn(
            "targets/drill-connection-split-is-reconciliation-stable",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_the_endpoint_config_map_is_uncommitted_and_unreconciled(self) -> None:
        config_map = self.credentials["drill_endpoint_config_map"]

        self.assertEqual(config_map["key"], "DB_HOST")
        self.assertIs(config_map["committed_to_git"], False)
        self.assertIs(config_map["written_back_to_secrets_manager"], False)
        self.assertIs(config_map["deleted_at_cleanup"], True)
        self.assertTrue(config_map["reconciled_by"].strip())
        # The endpoint does not exist until the restore has been verified.
        self.assertIn("verification", config_map["created_after"])

    def test_committing_the_restored_endpoint_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["drill_endpoint_config_map"][
            "committed_to_git"
        ] = True

        self.assertIn(
            "targets/drill-connection-split-is-reconciliation-stable",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_an_endpoint_config_map_that_outlives_the_drill_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["drill_endpoint_config_map"][
            "deleted_at_cleanup"
        ] = False

        self.assertIn(
            "targets/drill-connection-split-is-reconciliation-stable",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_the_workloads_take_host_and_credentials_from_separate_objects(self) -> None:
        consumption = self.credentials["consumption"]

        self.assertEqual(consumption["host_from"], "drill_endpoint_config_map")
        self.assertEqual(consumption["credential_fields_from"], "drill_secret")
        self.assertIn("configMapKeyRef", consumption["mechanism"])
        self.assertIn("secretKeyRef", consumption["mechanism"])
        self.assertGreaterEqual(len(consumption["consumers"]), 2)

    def test_taking_the_host_from_the_drill_secret_is_rejected(self) -> None:
        contract = self.mutated()
        contract["restore_target"]["credentials"]["consumption"]["host_from"] = (
            "drill_secret"
        )

        self.assertIn(
            "targets/drill-connection-split-is-reconciliation-stable",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_missing_endpoint_config_map_is_a_hard_error(self) -> None:
        contract = self.mutated()
        del contract["restore_target"]["credentials"]["drill_endpoint_config_map"]

        with self.assertRaises(preflight.DrillContractError):
            preflight.run_checks(contract, ROOT)

    def test_the_drill_signer_names_only_the_drill_endpoint(self) -> None:
        signer = self.contract["rejection_test"]["signer"]

        self.assertEqual(
            signer["connection_secret"],
            "the drill-only connection Secret, never the live one",
        )
        self.assertIn("drill endpoint ConfigMap", signer["endpoint_config_map"])


class SchemaEvidenceTests(unittest.TestCase):
    """Schema evidence has to pin objects, not count tables."""

    def setUp(self) -> None:
        self.contract = preflight.load_contract(CONTRACT_PATH)

    def mutated(self) -> dict:
        return copy.deepcopy(self.contract)

    def test_the_declared_table_digest_matches_the_declared_table_set(self) -> None:
        schema = self.contract["verification"]["schema"]

        self.assertEqual(
            schema["expected_table_digest"],
            preflight.table_inventory_digest(schema["expected_tables"]),
        )

    def test_seven_arbitrary_tables_cannot_satisfy_the_inventory(self) -> None:
        contract = self.mutated()
        schema = contract["verification"]["schema"]
        schema["expected_tables"] = [
            f"table_{index}" for index in range(len(schema["expected_tables"]))
        ]

        # Same count, different tables: the digest no longer matches.
        self.assertIn(
            "verification/schema-inventory-digest",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_stale_table_digest_is_rejected(self) -> None:
        contract = self.mutated()
        contract["verification"]["schema"]["expected_table_digest"] = "0" * 32

        self.assertIn(
            "verification/schema-inventory-digest",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_the_web3signer_schema_version_is_pinned_to_twelve(self) -> None:
        schema = self.contract["verification"]["schema"]

        self.assertEqual(schema["expected_database_version"], 12)
        self.assertEqual(schema["expected_migration_count"], 12)
        self.assertIn(
            "database_version_value equals 12",
            " ".join(" ".join(schema["pass_conditions"]).split()),
        )

    def test_disagreeing_version_anchors_are_rejected(self) -> None:
        contract = self.mutated()
        contract["verification"]["schema"]["expected_database_version"] = 11

        self.assertIn(
            "verification/schema-version-anchors",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_dropping_the_database_version_query_is_rejected(self) -> None:
        contract = self.mutated()
        schema = contract["verification"]["schema"]
        schema["queries"] = [
            query for query in schema["queries"] if query["id"] != "database-version-row"
        ]

        self.assertIn(
            "verification/schema-version-anchors",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_dropping_a_slashing_critical_object_is_rejected(self) -> None:
        contract = self.mutated()
        schema = contract["verification"]["schema"]
        schema["slashing_critical_objects"] = [
            entry
            for entry in schema["slashing_critical_objects"]
            if entry["table"] != "signed_attestations"
        ]

        self.assertIn(
            "verification/slashing-critical-objects-declared",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_declared_object_outside_the_table_inventory_is_rejected(self) -> None:
        contract = self.mutated()
        contract["verification"]["schema"]["slashing_critical_objects"].append(
            {
                "table": "not_a_web3signer_table",
                "unique_keys": [{"columns": ["id"], "enforces": "nothing real"}],
            }
        )

        self.assertIn(
            "verification/slashing-critical-objects-declared",
            failed(preflight.run_checks(contract, ROOT)),
        )


class ContinuityCoverageTests(unittest.TestCase):
    """Continuity is exact, salted, core-only, and covers every safety table."""

    def setUp(self) -> None:
        self.contract = preflight.load_contract(CONTRACT_PATH)

    def mutated(self) -> dict:
        return copy.deepcopy(self.contract)

    def test_every_safety_bearing_table_has_a_digest_query(self) -> None:
        continuity = self.contract["verification"]["continuity"]
        query_ids = {query["id"] for query in continuity["queries"]}

        for table in sorted(preflight.SAFETY_BEARING_TABLES):
            with self.subTest(table=table):
                self.assertIn(table, continuity["covered_tables"])
                self.assertIn(f"{table.replace('_', '-')}-digest", query_ids)

    def test_dropping_a_safety_bearing_table_is_rejected(self) -> None:
        contract = self.mutated()
        continuity = contract["verification"]["continuity"]
        continuity["covered_tables"] = [
            table for table in continuity["covered_tables"] if table != "low_watermarks"
        ]

        self.assertIn(
            "verification/continuity-covers-every-safety-table",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_covered_table_without_a_digest_query_is_rejected(self) -> None:
        contract = self.mutated()
        continuity = contract["verification"]["continuity"]
        continuity["queries"] = [
            query for query in continuity["queries"] if query["id"] != "metadata-digest"
        ]

        self.assertIn(
            "verification/continuity-covers-every-safety-table",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_tolerating_extra_rows_is_rejected(self) -> None:
        contract = self.mutated()
        continuity = contract["verification"]["continuity"]
        continuity["pass_conditions"].append(
            "restored signed_blocks_rows is greater than or equal to the source value"
        )

        self.assertIn(
            "verification/continuity-requires-exact-equality",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_a_non_exact_comparison_is_rejected(self) -> None:
        contract = self.mutated()
        contract["verification"]["continuity"]["comparison"] = "at-least"

        self.assertIn(
            "verification/continuity-requires-exact-equality",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_an_unsalted_digest_is_rejected(self) -> None:
        contract = self.mutated()
        continuity = contract["verification"]["continuity"]
        query = next(
            item for item in continuity["queries"] if item["id"] == "validators-digest"
        )
        query["sql"] = query["sql"].replace(":drill_salt || ", "")

        self.assertIn(
            "verification/digests-are-salted",
            failed(preflight.run_checks(contract, ROOT)),
        )

    def test_no_query_depends_on_an_extension_the_migrations_do_not_create(
        self,
    ) -> None:
        verification = self.contract["verification"]
        for section in ("schema", "continuity"):
            for query in verification[section]["queries"]:
                with self.subTest(query=query["id"]):
                    for function in preflight.NON_CORE_FUNCTIONS:
                        self.assertNotIn(f"{function}(", query["sql"])

    def test_reintroducing_a_pgcrypto_digest_is_rejected(self) -> None:
        contract = self.mutated()
        continuity = contract["verification"]["continuity"]
        query = next(
            item for item in continuity["queries"] if item["id"] == "validators-digest"
        )
        # `pgcrypto` is not created by the pinned migrations, so this would fail
        # at drill time on a database the drill may not alter.
        query["sql"] = query["sql"].replace(
            "md5(:drill_salt || t::text)",
            "encode(hmac(t::text, :drill_salt, 'sha256'), 'hex')",
        )

        self.assertIn(
            "query/validators-digest", failed(preflight.run_checks(contract, ROOT))
        )


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

    def test_nested_aggregate_over_a_salted_core_digest_is_accepted(self) -> None:
        sql = (
            "SELECT count(*) AS covered_rows, "
            "md5(string_agg(row_digest, ',' ORDER BY row_digest COLLATE \"C\")) "
            "AS covered_digest "
            "FROM (SELECT md5(:drill_salt || v::text) AS row_digest "
            "FROM validators v) AS covered"
        )

        self.assertTrue(self.check(sql).passed)

    def test_a_pgcrypto_digest_is_rejected(self) -> None:
        # The pinned Web3Signer migrations do not create pgcrypto, so hmac() is
        # unavailable and reaching for it would mean installing an extension.
        result = self.check(
            "SELECT count(*) AS covered_rows, "
            "md5(string_agg(row_digest, ',' ORDER BY row_digest)) AS covered_digest "
            "FROM (SELECT encode(hmac(v.public_key::text, :salt, 'sha256'), 'hex') "
            "AS row_digest FROM validators v) AS covered"
        )

        self.assertFalse(result.passed)
        self.assertIn("hmac()", result.detail)


class ContractLoadingTests(unittest.TestCase):
    """A safety flag may not depend on which duplicated line wins."""

    def load(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.yaml"
            path.write_text(text, encoding="utf-8")
            return preflight.load_contract(path)

    def test_a_duplicate_key_is_rejected_rather_than_silently_overridden(self) -> None:
        text = (
            "apiVersion: platform.galaxy-lab/v1\n"
            "kind: SlashingRecoveryDrillContract\n"
            "gates:\n"
            "  - id: conflicting-duty-rejection\n"
            "    incurs_aws_cost: true\n"
            "    incurs_aws_cost: false\n"
        )

        with self.assertRaises(preflight.DrillContractError) as raised:
            self.load(text)

        self.assertIn("duplicate key 'incurs_aws_cost'", str(raised.exception))

    def test_a_duplicate_top_level_key_is_rejected(self) -> None:
        text = (
            "apiVersion: platform.galaxy-lab/v1\n"
            "kind: SlashingRecoveryDrillContract\n"
            "status:\n"
            "  executed: false\n"
            "status:\n"
            "  executed: true\n"
        )

        with self.assertRaises(preflight.DrillContractError):
            self.load(text)

    def test_the_checked_in_contract_has_no_duplicate_key(self) -> None:
        # PyYAML would have kept the last value; this loader would have raised.
        self.assertEqual(
            preflight.load_contract(CONTRACT_PATH)["metadata"]["name"],
            "rds-slashing-recovery-drill",
        )


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

    def test_the_runbook_does_not_claim_the_restore_inherits_tls(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        self.assertNotIn("parameter group's TLS requirement", runbook)
        self.assertIn(
            "A point-in-time restore does **not** reproduce the source's "
            "placement, networking, or parameters",
            runbook,
        )

    def test_the_runbook_keeps_the_live_credential_path_untouched(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        self.assertIn("not modified, not repointed, and not deleted", runbook)
        self.assertIn("distinct target name", runbook)

    def test_the_runbook_restore_call_is_runnable_as_written(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        self.assertIn("`--no-deletion-protection`", runbook)
        self.assertNotIn("--deletion-protection false` | off", runbook)
        # The restore API does accept the parameter, and omitting it inherits the
        # source's retention, so the runbook must instruct an operator to pass it.
        self.assertIn("`--backup-retention-period 0`", runbook)
        self.assertNotIn("has no `--backup-retention-period` parameter", runbook)

    def test_the_runbook_gates_the_drill_on_the_installed_cli(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        # The operator-facing check is the installed command's own help, and the
        # documented response to a missing flag is upgrading, never omitting.
        self.assertIn(
            "aws rds restore-db-instance-to-point-in-time help "
            "| grep -- --backup-retention-period",
            runbook,
        )
        self.assertIn("stop here and upgrade the AWS CLI", runbook)
        self.assertIn("Do not drop the parameter to make the command run.", runbook)

    def test_the_runbook_states_the_documented_single_az_default(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        self.assertIn("single-AZ deployment by default", runbook)
        self.assertNotIn("the restore API states no default for Multi-AZ", runbook)
        # Documented default or not, the flag stays in the reviewed call.
        self.assertIn("`--no-multi-az`", runbook)

    def test_the_runbook_aborts_on_a_backup_retention_mismatch(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        self.assertIn("If it is not zero, **abort and run gate 8**", runbook)
        self.assertIn("Do not modify the instance.", runbook)

    def test_the_runbook_splits_the_host_from_the_credential_fields(self) -> None:
        runbook = " ".join(RUNBOOK_PATH.read_text(encoding="utf-8").split())

        self.assertIn("does not project `host`", runbook)
        self.assertIn("`configMapKeyRef`", runbook)
        self.assertIn("`secretKeyRef`", runbook)
        # Cleanup has to remove both halves, or the drill leaves state behind.
        self.assertIn(
            "Delete the drill-only connection `ExternalSecret` and the Secret it "
            "created, and the drill endpoint `ConfigMap`",
            runbook,
        )

    def test_the_runbook_repeats_no_numbered_step(self) -> None:
        # A duplicated procedure step reads as two actions where the operator
        # should take one.
        steps: dict[str, int] = {}
        for line in RUNBOOK_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped[:1].isdigit() or ". " not in stripped[:4]:
                continue
            text = stripped.split(". ", 1)[1]
            steps[text] = steps.get(text, 0) + 1

        self.assertEqual([text for text, count in steps.items() if count > 1], [])


if __name__ == "__main__":
    unittest.main()
