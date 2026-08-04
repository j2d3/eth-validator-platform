"""Contracts for the trusted-local Web3Signer database bootstrap."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

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
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION", command)

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


if __name__ == "__main__":
    unittest.main()
