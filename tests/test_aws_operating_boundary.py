"""Contracts for the one-cluster, local-apply AWS operating boundary."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TERRAFORM_ROOT = ROOT / "terraform"
EKS_ROOT = TERRAFORM_ROOT / "environments" / "dev"


class AwsOperatingBoundaryTests(unittest.TestCase):
    def test_github_actions_cannot_mutate_terraform(self) -> None:
        """The v1 AWS foundation is applied by a trusted local operator."""

        payload = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.y*ml"))
        )
        mutating_commands = re.compile(
            r"\bterraform(?:\s+-chdir=\S+)?\s+"
            r"(?:apply|destroy|import|taint|untaint|state\s+(?:mv|rm|push)|force-unlock)\b",
            re.IGNORECASE,
        )
        self.assertIsNone(mutating_commands.search(payload))

    def test_cloud_root_is_aws_eks_not_google_gke(self) -> None:
        payload = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(TERRAFORM_ROOT.rglob("*.tf"))
        ).lower()

        self.assertIn('terraform-aws-modules/eks/aws', payload)
        self.assertIn('resource "aws_eks_pod_identity_association"', payload)
        self.assertIn('resource "aws_secretsmanager_secret"', payload)
        for forbidden in (
            'provider "google"',
            'provider "google-beta"',
            'resource "google_',
            'module "gke"',
            'google_container_cluster',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, payload)

    def test_documentation_names_the_writer_boundaries(self) -> None:
        # The writer-boundary content moved from the top-level README to the
        # architecture overview when the docs tree was reorganized; the top
        # README now points readers there. Assertions target the durable
        # location.
        architecture = " ".join(
            (ROOT / "docs" / "architecture" / "system-overview.md")
            .read_text(encoding="utf-8")
            .split()
        )
        environment = " ".join(
            (EKS_ROOT / "README.md").read_text(encoding="utf-8").split()
        )

        self.assertIn("Amazon EKS is the only cloud Kubernetes target", architecture)
        self.assertIn("Terraform, run from a trusted operator workstation", architecture)
        self.assertIn("There is intentionally no GitHub Actions Terraform apply/destroy workflow", environment)
        self.assertIn("Flux is the continuous writer for in-cluster applications", environment)


if __name__ == "__main__":
    unittest.main()
