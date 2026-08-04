"""Contracts for the EKS Web3Signer RDS and secret foundation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EKS_ROOT = ROOT / "terraform" / "environments" / "dev"
MAIN = (EKS_ROOT / "main.tf").read_text(encoding="utf-8")
SIGNER = (EKS_ROOT / "signer-foundation.tf").read_text(encoding="utf-8")
VARIABLES = (EKS_ROOT / "variables.tf").read_text(encoding="utf-8")
OUTPUTS = (EKS_ROOT / "outputs.tf").read_text(encoding="utf-8")
README = (EKS_ROOT / "README.md").read_text(encoding="utf-8")
README_COMPACT = " ".join(README.split())


def resource_blocks(resource_type: str) -> dict[str, str]:
    """Return compact top-level Terraform resource blocks keyed by name."""

    matches = re.finditer(
        rf'^resource "{re.escape(resource_type)}" "(?P<name>[^"]+)" '
        r'\{(?P<body>.*?)(?=^(?:resource|data|locals|module|output|variable) "|\Z)',
        SIGNER,
        flags=re.MULTILINE | re.DOTALL,
    )
    return {
        match.group("name"): " ".join(match.group("body").split())
        for match in matches
    }


def resource_block(resource_type: str, name: str) -> str:
    """Return one top-level Terraform resource block for string assertions."""

    blocks = resource_blocks(resource_type)
    if name not in blocks:
        raise AssertionError(f"resource {resource_type}.{name} is not declared")
    return blocks[name]


def variable_block(name: str) -> str:
    match = re.search(
        rf'^variable "{re.escape(name)}" \{{(?P<body>.*?)(?=^variable "|\Z)',
        VARIABLES,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"variable {name!r} is not declared")
    return " ".join(match.group("body").split())


def data_block(name: str) -> str:
    match = re.search(
        rf'^data "aws_iam_policy_document" "{re.escape(name)}" '
        r'\{(?P<body>.*?)(?=^(?:resource|data|locals|module|output|variable) "|\Z)',
        SIGNER,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"data aws_iam_policy_document.{name} is not declared")
    return " ".join(match.group("body").split())


class EksSignerFoundationContractTests(unittest.TestCase):
    def test_database_subnets_are_isolated_and_private(self) -> None:
        compact_main = " ".join(MAIN.split())
        database = resource_block("aws_db_instance", "web3signer")

        self.assertIn("database_subnets = [", MAIN)
        self.assertIn("create_database_subnet_route_table = true", compact_main)
        self.assertIn("create_database_internet_gateway_route = false", compact_main)
        self.assertIn("create_database_nat_gateway_route = false", compact_main)
        self.assertIn("publicly_accessible = false", database)
        self.assertIn("multi_az = false", database)
        self.assertIn("db_subnet_group_name = module.vpc.database_subnet_group_name", database)

    def test_rds_ingress_is_exactly_signer_and_migration_pod_groups(self) -> None:
        ingress_blocks = resource_blocks("aws_vpc_security_group_ingress_rule")
        database_ingress = {
            name: body
            for name, body in ingress_blocks.items()
            if re.search(
                r"(?:^| )security_group_id = aws_security_group\.web3signer_database\.id(?: |$)",
                body,
            )
        }

        self.assertEqual(
            set(database_ingress),
            {"database_from_web3signer", "database_from_web3signer_migration"},
        )
        self.assertEqual(
            {
                reference
                for body in database_ingress.values()
                for reference in re.findall(
                    r"referenced_security_group_id = ([^ ]+)", body
                )
            },
            {
                "aws_security_group.web3signer_pod.id",
                "aws_security_group.web3signer_migration_pod.id",
            },
        )
        for body in database_ingress.values():
            self.assertIn("from_port = local.web3signer_database_port", body)
            self.assertIn("to_port = local.web3signer_database_port", body)
            self.assertNotIn("module.eks.node_security_group_id", body)

        signer_egress = resource_block(
            "aws_vpc_security_group_egress_rule", "web3signer_to_database"
        )
        self.assertIn(
            "referenced_security_group_id = aws_security_group.web3signer_database.id",
            signer_egress,
        )

    def test_migration_pod_group_has_only_postgresql_and_dns_flows(self) -> None:
        egress_blocks = resource_blocks("aws_vpc_security_group_egress_rule")
        migration_egress = {
            name: body
            for name, body in egress_blocks.items()
            if re.search(
                r"(?:^| )security_group_id = aws_security_group\.web3signer_migration_pod\.id(?: |$)",
                body,
            )
        }
        self.assertEqual(
            set(migration_egress),
            {
                "web3signer_migration_to_database",
                "web3signer_migration_dns_tcp",
                "web3signer_migration_dns_udp",
            },
        )
        self.assertIn(
            "referenced_security_group_id = aws_security_group.web3signer_database.id",
            migration_egress["web3signer_migration_to_database"],
        )
        for protocol in ("tcp", "udp"):
            dns = migration_egress[f"web3signer_migration_dns_{protocol}"]
            self.assertIn("referenced_security_group_id = module.eks.node_security_group_id", dns)
            self.assertIn("from_port = 53", dns)
            self.assertIn(f'ip_protocol = "{protocol}"', dns)

        migration_ingress = {
            name
            for name, body in resource_blocks(
                "aws_vpc_security_group_ingress_rule"
            ).items()
            if re.search(
                r"(?:^| )security_group_id = aws_security_group\.web3signer_migration_pod\.id(?: |$)",
                body,
            )
        }
        self.assertEqual(migration_ingress, set())

        migration_as_source = {
            name
            for name, body in resource_blocks(
                "aws_vpc_security_group_ingress_rule"
            ).items()
            if "referenced_security_group_id = aws_security_group.web3signer_migration_pod.id"
            in body
        }
        self.assertEqual(
            migration_as_source,
            {
                "database_from_web3signer_migration",
                "nodes_dns_from_web3signer_migration_tcp",
                "nodes_dns_from_web3signer_migration_udp",
            },
        )

    def test_security_groups_for_pods_are_declared_before_the_overlay(self) -> None:
        compact = " ".join(MAIN.split())

        self.assertIn("AmazonEKSVPCResourceController", compact)
        self.assertIn('enableNetworkPolicy = "true"', compact)
        self.assertIn('ENABLE_POD_ENI = "true"', compact)
        self.assertIn('NETWORK_POLICY_ENFORCING_MODE = "standard"', compact)
        self.assertIn('POD_SECURITY_GROUP_ENFORCING_MODE = "standard"', compact)
        self.assertIn('output "web3signer_pod_security_group_id"', OUTPUTS)
        self.assertIn('output "web3signer_migration_pod_security_group_id"', OUTPUTS)
        self.assertIn(
            "value = aws_security_group.web3signer_migration_pod.id",
            " ".join(OUTPUTS.split()),
        )
        self.assertIn("SecurityGroupPolicy", README_COMPACT)
        self.assertIn(
            "Those adapters now reconcile before Web3Signer is admitted",
            README_COMPACT,
        )

    def test_rds_is_small_encrypted_and_bounded(self) -> None:
        database = resource_block("aws_db_instance", "web3signer")

        self.assertIn('engine = "postgres"', database)
        self.assertIn("engine_version = var.rds_postgres_major_version", database)
        self.assertIn("instance_class = var.rds_instance_class", database)
        self.assertIn('storage_type = "gp3"', database)
        self.assertIn("storage_encrypted = true", database)
        self.assertIn("allocated_storage = var.rds_allocated_storage_gib", database)
        self.assertIn("max_allocated_storage = var.rds_max_allocated_storage_gib", database)
        self.assertIn('default = "db.t4g.micro"', variable_block("rds_instance_class"))
        self.assertIn("default = 20", variable_block("rds_allocated_storage_gib"))
        self.assertIn("default = 100", variable_block("rds_max_allocated_storage_gib"))
        self.assertIn("performance_insights_enabled = false", database)
        self.assertIn("monitoring_interval = 0", database)

    def test_rds_requires_ssl_without_a_perpetual_apply_method_diff(self) -> None:
        parameter_group = resource_block("aws_db_parameter_group", "web3signer")

        self.assertIn('name = "rds.force_ssl"', " ".join(parameter_group.split()))
        self.assertIn('value = "1"', " ".join(parameter_group.split()))
        self.assertIn(
            'apply_method = "pending-reboot"',
            " ".join(parameter_group.split()),
        )

    def test_slashing_history_has_pitr_and_destroy_guards(self) -> None:
        database = resource_block("aws_db_instance", "web3signer")

        self.assertIn("backup_retention_period = var.rds_backup_retention_days", database)
        self.assertIn("deletion_protection = var.rds_deletion_protection", database)
        self.assertIn("skip_final_snapshot = var.rds_skip_final_snapshot", database)
        self.assertIn("final_snapshot_identifier = var.rds_skip_final_snapshot ? null", database)
        self.assertIn("delete_automated_backups = false", database)
        self.assertIn("copy_tags_to_snapshot = true", database)
        self.assertIn("default = 7", variable_block("rds_backup_retention_days"))
        self.assertIn("default = true", variable_block("rds_deletion_protection"))
        self.assertIn("default = false", variable_block("rds_skip_final_snapshot"))

    def test_terraform_never_declares_secret_values(self) -> None:
        terraform = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(EKS_ROOT.glob("*.tf"))
        )
        database = resource_block("aws_db_instance", "web3signer")

        self.assertNotIn('resource "aws_secretsmanager_secret_version"', terraform)
        self.assertNotRegex(terraform, r"(?m)^\s*(?:password|secret_string)\s*=")
        self.assertIn("manage_master_user_password = true", database)
        self.assertIn('resource "aws_secretsmanager_secret" "web3signer_database"', SIGNER)
        self.assertIn('resource "aws_secretsmanager_secret" "web3signer_signing_key"', SIGNER)
        self.assertIn("No password expression or secret version enters Terraform state", SIGNER)

    def test_database_secret_matches_flux_source_contract(self) -> None:
        database_secret = resource_block("aws_secretsmanager_secret", "web3signer_database")

        self.assertIn('name = "${local.name}/signing/web3signer-database"', database_secret)
        self.assertIn("canonical source JSON fields are host, port, database, username, and", SIGNER)
        self.assertIn("projects the canonical `database` property to target `dbname`", README)
        self.assertIn("`.../signing/web3signer-database`", README)
        self.assertIn('database_connection = aws_secretsmanager_secret.web3signer_database.arn', OUTPUTS)

    def test_external_secrets_uses_scoped_assume_roles(self) -> None:
        base_policy = re.search(
            r'data "aws_iam_policy_document" "external_secrets" '
            r'\{(?P<body>.*?)\n\}',
            MAIN,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(base_policy)
        assert base_policy is not None
        compact_base = " ".join(base_policy.group("body").split())

        self.assertIn(
            'actions = ["sts:AssumeRole", "sts:TagSession"]', compact_base
        )
        self.assertNotIn("sts:SetSourceIdentity", compact_base)
        self.assertNotIn("secretsmanager:GetSecretValue", compact_base)

        reader_trust = data_block("external_secrets_reader_trust")
        self.assertIn(
            'actions = ["sts:AssumeRole", "sts:TagSession"]', reader_trust
        )
        self.assertNotIn("sts:SetSourceIdentity", reader_trust)
        self.assertIn(
            "identifiers = [aws_iam_role.external_secrets.arn]", reader_trust
        )

        for role_name in (
            "external_secrets_engine_reader",
            "external_secrets_database_reader",
            "external_secrets_signing_reader",
        ):
            reader_role = resource_block("aws_iam_role", role_name)
            self.assertIn(
                "data.aws_iam_policy_document.external_secrets_reader_trust.json",
                reader_role,
            )

        engine = resource_block("aws_iam_role_policy", "external_secrets_engine_reader")
        database = resource_block("aws_iam_role_policy", "external_secrets_database_reader")
        signing = resource_block("aws_iam_role_policy", "external_secrets_signing_reader")
        self.assertIn("data.aws_iam_policy_document.external_secrets_engine_reader.json", engine)
        self.assertIn("data.aws_iam_policy_document.external_secrets_database_reader.json", database)
        self.assertIn("data.aws_iam_policy_document.external_secrets_signing_reader.json", signing)

        engine_data = data_block("external_secrets_engine_reader")
        database_data = data_block("external_secrets_database_reader")
        signing_data = data_block("external_secrets_signing_reader")
        self.assertIn("aws_secretsmanager_secret.engine_jwt.arn", engine_data)
        self.assertNotIn("aws_secretsmanager_secret.web3signer_database.arn", engine_data)
        self.assertNotIn("aws_secretsmanager_secret.web3signer_signing_key.arn", engine_data)

        self.assertIn("aws_secretsmanager_secret.web3signer_database.arn", database_data)
        self.assertNotIn("aws_secretsmanager_secret.web3signer_signing_key.arn", database_data)
        self.assertNotIn("aws_secretsmanager_secret.engine_jwt.arn", database_data)

        self.assertIn("aws_secretsmanager_secret.web3signer_signing_key.arn", signing_data)
        self.assertNotIn("aws_secretsmanager_secret.web3signer_database.arn", signing_data)
        self.assertNotIn("aws_secretsmanager_secret.engine_jwt.arn", signing_data)
        for policy in (engine_data, database_data, signing_data):
            self.assertNotIn('resources = ["*"]', policy)

        self.assertIn("aws_iam_role.external_secrets_engine_reader.arn", compact_base)
        self.assertIn("aws_iam_role.external_secrets_database_reader.arn", compact_base)
        self.assertIn("aws_iam_role.external_secrets_signing_reader.arn", compact_base)

        self.assertNotIn('resource "aws_eks_pod_identity_association" "web3signer"', SIGNER)
        self.assertIn("Web3Signer receives no Pod Identity association", README_COMPACT)

    def test_outputs_expose_only_nonsecret_overlay_inputs(self) -> None:
        output = re.search(
            r'output "web3signer_database" \{(?P<body>.*?)(?=^output "|\Z)',
            OUTPUTS,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(output)
        assert output is not None
        compact = " ".join(output.group("body").split())
        outputs_compact = " ".join(OUTPUTS.split())

        self.assertIn("vpc_cidr = module.vpc.vpc_cidr_block", compact)
        self.assertIn("ca_cert_identifier = aws_db_instance.web3signer.ca_cert_identifier", compact)
        self.assertNotRegex(compact, r"(?<!master_)password")
        self.assertIn("engine = aws_iam_role.external_secrets_engine_reader.arn", outputs_compact)
        self.assertIn("database = aws_iam_role.external_secrets_database_reader.arn", outputs_compact)
        self.assertIn("signing = aws_iam_role.external_secrets_signing_reader.arn", outputs_compact)

    def test_documentation_distinguishes_live_readiness_from_production_qualification(self) -> None:
        self.assertIn("first validator duty produced an RDS-backed attestation operation", README_COMPACT)
        self.assertIn("Runtime evidence and remaining gates", README_COMPACT)
        self.assertIn("deliberately conflicting-signature rejection test", README_COMPACT)
        self.assertIn("AWS restarts a stopped DB instance after at most seven days", README_COMPACT)
        self.assertIn("snapshot → delete → restore", README_COMPACT)
        self.assertIn("`sslmode=verify-full`", README_COMPACT)


if __name__ == "__main__":
    unittest.main()
