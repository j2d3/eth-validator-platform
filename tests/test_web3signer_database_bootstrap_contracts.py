"""Contracts for the trusted-local Web3Signer database bootstrap."""

from __future__ import annotations

import json
import re
import unittest
from importlib import util
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hack" / "bootstrap-web3signer-database.py"
FIXTURE = ROOT / "hack" / "qualification" / "web3signer-database-bootstrap.yaml"


class Web3SignerDatabaseBootstrapContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")
        cls.fixture = FIXTURE.read_text(encoding="utf-8")
        cls.documents = list(yaml.safe_load_all(cls.fixture.replace(
            "${WEB3SIGNER_DATABASE_VPC_CIDR}", "10.42.0.0/16"
        )))
        cls.by_kind = {document["kind"]: document for document in cls.documents}
        spec = util.spec_from_file_location("web3signer_database_bootstrap", SCRIPT)
        assert spec and spec.loader
        cls.bootstrap = util.module_from_spec(spec)
        spec.loader.exec_module(cls.bootstrap)

    def test_fixture_is_one_exact_job_and_network_policy(self) -> None:
        self.assertEqual(set(self.by_kind), {"Job", "NetworkPolicy"})
        for document in self.documents:
            self.assertEqual(document["metadata"]["name"], "web3signer-database-bootstrap")
            self.assertEqual(document["metadata"]["namespace"], "database")

    def test_job_selects_only_the_migration_pod_security_group(self) -> None:
        labels = self.by_kind["Job"]["spec"]["template"]["metadata"]["labels"]
        self.assertEqual(labels["app.kubernetes.io/name"], "web3signer-schema")
        self.assertEqual(labels["app.kubernetes.io/component"], "database-migration")
        self.assertEqual(
            labels["platform.galaxy-lab/qualification"], "database-bootstrap"
        )
        self.assertIn(
            '"web3signer-schema"',
            self.script.split("def ensure_bootstrap_boundary", 1)[1],
        )
        self.assertIn("expected_group not in groups", self.script)

    def test_job_is_digest_pinned_and_restricted(self) -> None:
        job = self.by_kind["Job"]
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertRegex(container["image"], r"^postgres:[^@]+@sha256:[0-9a-f]{64}$")
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["restartPolicy"], "Never")
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        security = container["securityContext"]
        self.assertFalse(security["allowPrivilegeEscalation"])
        self.assertTrue(security["readOnlyRootFilesystem"])
        self.assertEqual(security["capabilities"]["drop"], ["ALL"])

    def test_sql_reads_passwords_from_environment_not_process_arguments(self) -> None:
        command = self.by_kind["Job"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("\\getenv app_password APP_PASSWORD", command)
        self.assertIn('export PGPASSWORD="$MASTER_PASSWORD"', command)
        self.assertNotIn("--password=", command)
        self.assertNotIn("--set=app_password", command)
        self.assertIn("ALTER ROLE %I WITH LOGIN PASSWORD %L", command)
        self.assertNotIn("ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER", command)

    def test_sql_refuses_a_preexisting_privileged_role_before_altering_it(self) -> None:
        command = self.by_kind["Job"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        safety_check = command.index("AS role_attributes_are_safe")
        alter_role = command.index("ALTER ROLE %I WITH LOGIN PASSWORD %L")
        self.assertLess(safety_check, alter_role)
        for attribute in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication"):
            self.assertIn(f"NOT {attribute}", command)

    def test_tls_and_egress_are_fail_closed(self) -> None:
        command = self.by_kind["Job"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("PGSSLMODE=verify-full", command)
        self.assertIn("PGSSLROOTCERT=/etc/rds-ca/global-bundle.pem", command)
        policy = self.by_kind["NetworkPolicy"]["spec"]
        self.assertEqual(policy["policyTypes"], ["Ingress", "Egress"])
        self.assertEqual(policy["ingress"], [])
        self.assertEqual(policy["egress"][0]["to"][0]["ipBlock"]["cidr"], "10.42.0.0/16")
        self.assertEqual(policy["egress"][0]["ports"], [{"port": 5432, "protocol": "TCP"}])

    def test_tool_never_writes_secret_material_to_a_local_file(self) -> None:
        forbidden = (
            "NamedTemporaryFile",
            "TemporaryDirectory",
            "mkstemp",
            "write_text(",
            "write_bytes(",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.script)
        self.assertIn('"--cli-input-json", "file:///dev/stdin"', self.script)
        self.assertNotRegex(self.script, re.compile(r"--secret-string[^\n]*app_password"))

    def test_tool_refuses_implicit_rotation_and_cleans_exact_names(self) -> None:
        self.assertIn("refusing implicit rotation", self.script)
        self.assertIn('"job", RESOURCE_NAME', self.script)
        self.assertIn('"networkpolicy", RESOURCE_NAME', self.script)
        self.assertIn('"secret", RESOURCE_NAME', self.script)
        self.assertIn('"configmap", CA_CONFIGMAP', self.script)
        self.assertNotIn("--all", self.script)

    def test_rds_managed_master_secret_may_be_credentials_only(self) -> None:
        observed = {"username": "web3signer_admin", "password": "x" * 32}
        with patch.object(
            self.bootstrap,
            "run",
            return_value=CompletedProcess([], 0, stdout=json.dumps(observed)),
        ):
            result = self.bootstrap.load_master_secret(
                "secret-arn", {"address": "db.example", "port": 5432}
            )
        self.assertEqual(result, observed)

    def test_optional_master_secret_routing_fields_must_match_terraform(self) -> None:
        base = {"username": "web3signer_admin", "password": "x" * 32}
        database = {"address": "db.example", "port": 5432}
        for override in ({"host": "stale.example"}, {"port": 6432}, {"port": "bad"}):
            with self.subTest(override=override), patch.object(
                self.bootstrap,
                "run",
                return_value=CompletedProcess(
                    [], 0, stdout=json.dumps(base | override)
                ),
            ):
                with self.assertRaises(self.bootstrap.BootstrapError):
                    self.bootstrap.load_master_secret("secret-arn", database)

    def test_branch_eni_annotation_accepts_the_observed_single_record_array(self) -> None:
        annotation = json.dumps([{"eniId": "eni-0123456789abcdef0"}])
        self.assertEqual(
            self.bootstrap.parse_branch_eni_id(annotation),
            "eni-0123456789abcdef0",
        )

    def test_branch_eni_annotation_rejects_ambiguous_or_malformed_shapes(self) -> None:
        invalid = (
            "not-json",
            json.dumps([]),
            json.dumps([{"eniId": "eni-one"}, {"eniId": "eni-two"}]),
            json.dumps(["eni-0123456789abcdef0"]),
            json.dumps({}),
            json.dumps("eni-0123456789abcdef0"),
        )
        for annotation in invalid:
            with self.subTest(annotation=annotation):
                with self.assertRaises(self.bootstrap.BootstrapError):
                    self.bootstrap.parse_branch_eni_id(annotation)


if __name__ == "__main__":
    unittest.main()
