"""Contracts for the isolated, cost-conscious project ECR repository."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EKS_ROOT = ROOT / "terraform" / "environments" / "dev"
ECR = (EKS_ROOT / "ecr.tf").read_text(encoding="utf-8")
OUTPUTS = (EKS_ROOT / "outputs.tf").read_text(encoding="utf-8")
README = (EKS_ROOT / "README.md").read_text(encoding="utf-8")


class EcrRepositoryContractTests(unittest.TestCase):
    def test_repository_is_isolated_immutable_and_encrypted(self) -> None:
        compact = " ".join(ECR.split())

        self.assertEqual(ECR.count('resource "aws_ecr_repository"'), 1)
        self.assertIn('name = "${local.name}/portal"', compact)
        self.assertIn('image_tag_mutability = "IMMUTABLE"', compact)
        self.assertIn("force_delete = false", compact)
        self.assertIn('encryption_type = "AES256"', compact)
        self.assertIn('Component = "portal"', compact)

    def test_basic_scan_on_push_does_not_take_registry_or_inspector_ownership(
        self,
    ) -> None:
        terraform = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(EKS_ROOT.glob("*.tf"))
        )
        compact = " ".join(ECR.split())

        self.assertIn("image_scanning_configuration { scan_on_push = true", compact)
        self.assertNotIn("aws_ecr_registry_scanning_configuration", terraform)
        self.assertNotRegex(terraform, r'resource\s+"aws_inspector2_')
        self.assertIn("Do not manage", README)
        self.assertIn("unknown rather than zero", README)

    def test_lifecycle_bounds_untagged_age_and_total_inventory(self) -> None:
        compact = " ".join(ECR.split())

        self.assertEqual(ECR.count('resource "aws_ecr_lifecycle_policy"'), 1)
        self.assertIn('tagStatus = "untagged"', compact)
        self.assertIn('countType = "sinceImagePushed"', compact)
        self.assertIn('countUnit = "days"', compact)
        self.assertIn("countNumber = 14", compact)
        self.assertIn('tagStatus = "any"', compact)
        self.assertIn('countType = "imageCountMoreThan"', compact)
        self.assertIn("countNumber = 30", compact)
        self.assertIn("largest rulePriority number", ECR)
        self.assertIn("newest 30", README)

    def test_runbook_uses_exact_region_digest_and_bounded_scan_polling(self) -> None:
        for required in (
            '--repository-names "$REPOSITORY_NAME"',
            '--repository-name "$REPOSITORY_NAME"',
            '--image-id imageDigest="$IMAGE_DIGEST"',
            "get-registry-scanning-configuration",
            "SCAN_ON_PUSH",
            "COMPLETE",
            "UNSUPPORTED_IMAGE",
            'while [ "$attempt" -lt 30 ]',
        ):
            with self.subTest(required=required):
                self.assertIn(required, README)

    def test_outputs_are_for_trusted_operator_use(self) -> None:
        compact = " ".join(OUTPUTS.split())

        self.assertIn('output "portal_ecr_repository"', compact)
        self.assertIn("name = aws_ecr_repository.portal.name", compact)
        self.assertIn("url = aws_ecr_repository.portal.repository_url", compact)
        self.assertIn("arn = aws_ecr_repository.portal.arn", compact)
        self.assertNotRegex(ECR + OUTPUTS, r"[0-9]{12}\.dkr\.ecr\.")


if __name__ == "__main__":
    unittest.main()
