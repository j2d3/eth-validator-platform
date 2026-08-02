"""Contracts for bounded, zonal Ethereum capacity on the EKS lab."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EKS_ROOT = ROOT / "terraform" / "environments" / "dev"
MAIN = (EKS_ROOT / "main.tf").read_text(encoding="utf-8")
VARIABLES = (EKS_ROOT / "variables.tf").read_text(encoding="utf-8")
OUTPUTS = (EKS_ROOT / "outputs.tf").read_text(encoding="utf-8")
EXAMPLE = (EKS_ROOT / "terraform.tfvars.example").read_text(encoding="utf-8")
README = (EKS_ROOT / "README.md").read_text(encoding="utf-8")


def variable_block(name: str) -> str:
    """Return one top-level Terraform variable block without parsing HCL."""

    match = re.search(
        rf'^variable "{re.escape(name)}" \{{(?P<body>.*?)(?=^variable "|\Z)',
        VARIABLES,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"variable {name!r} is not declared")
    return match.group("body")


class EksCapacityContractTests(unittest.TestCase):
    def test_ethereum_capacity_is_one_managed_node_group_per_az(self) -> None:
        compact = " ".join(MAIN.split())

        self.assertIn(
            'for index, az in local.azs : "ethereum-${az}" => {', compact
        )
        self.assertIn("subnet_ids = [module.vpc.private_subnets[index]]", compact)
        self.assertIn("min_size = 0", compact)
        self.assertIn(
            "desired_size = index == var.ethereum_initial_active_az_index ? "
            "var.ethereum_initial_desired_size : 0",
            compact,
        )
        self.assertIn(
            '"platform.galaxy-lab/availability-zone" = az', compact
        )

    def test_spot_requires_an_explicit_operator_input(self) -> None:
        capacity = " ".join(variable_block("ethereum_capacity_type").split())

        self.assertIn('default = "ON_DEMAND"', capacity)
        self.assertRegex(EXAMPLE, r'(?m)^ethereum_capacity_type\s*=\s*"ON_DEMAND"$')
        self.assertIn("defaults to `ON_DEMAND`", README)
        self.assertIn("Change this reviewed operator input to", EXAMPLE)
        self.assertIn("SPOT only for the explicit testnet", EXAMPLE)

    def test_spot_pool_has_diverse_equivalent_instance_types(self) -> None:
        instance_types = variable_block("ethereum_instance_types")
        declared = re.findall(r'"(r[0-9]+[ai]\.2xlarge)"', instance_types)

        self.assertGreaterEqual(len(set(declared)), 3)
        self.assertTrue(any(instance.startswith("r8") for instance in declared))
        self.assertTrue(any("a.2xlarge" in instance for instance in declared))
        self.assertTrue(any("i.2xlarge" in instance for instance in declared))

    def test_capacity_and_root_storage_are_bounded(self) -> None:
        compact_main = " ".join(MAIN.split())
        desired = " ".join(
            variable_block("ethereum_initial_desired_size").split()
        )
        per_az_max = " ".join(variable_block("ethereum_max_size_per_az").split())
        system_root = " ".join(
            variable_block("system_root_volume_size_gib").split()
        )
        ethereum_root = " ".join(
            variable_block("ethereum_root_volume_size_gib").split()
        )

        self.assertIn(
            "contains([0, 1], var.ethereum_initial_desired_size)", desired
        )
        self.assertIn("var.ethereum_max_size_per_az <= 2", per_az_max)
        self.assertIn("default = 40", system_root)
        self.assertIn("default = 30", ethereum_root)
        self.assertEqual(compact_main.count('volume_type = "gp3"'), 2)
        self.assertIn("volume_size = var.system_root_volume_size_gib", compact_main)
        self.assertIn(
            "volume_size = var.ethereum_root_volume_size_gib", compact_main
        )

    def test_pause_output_and_documentation_preserve_the_ebs_az(self) -> None:
        compact_outputs = " ".join(OUTPUTS.split())
        compact_readme = " ".join(README.split())

        self.assertIn('output "ethereum_node_groups_by_az"', compact_outputs)
        self.assertIn("for az in local.azs : az =>", compact_outputs)
        self.assertNotRegex(OUTPUTS, r"(?m)^\s*desired_size\s*=")
        self.assertIn("Live state must be read from EKS", compact_readme)
        self.assertIn("intentionally places managed-node-group", compact_readme)
        self.assertIn("`desired_size` in Terraform `ignore_changes`", compact_readme)
        self.assertIn("A warm pause is not a Terraform variable edit", compact_readme)
        self.assertIn("wait until client pods are absent", compact_readme)
        self.assertIn("Resume sets one group to one in the PVC's AZ", compact_readme)
        self.assertIn("It is a warm pause, not zero cost", compact_readme)


if __name__ == "__main__":
    unittest.main()
