"""Contracts for the portal's dedicated Route 53 state boundary."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DNS_ROOT = ROOT / "terraform" / "environments" / "dns"


class PortalDnsContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.main = (DNS_ROOT / "main.tf").read_text(encoding="utf-8")
        self.versions = (DNS_ROOT / "versions.tf").read_text(encoding="utf-8")
        self.readme = (DNS_ROOT / "README.md").read_text(encoding="utf-8")

    def test_exact_hostname_has_no_wildcard(self) -> None:
        self.assertIn('portal_hostname  = "g.j2d3.com"', self.main)
        self.assertNotIn("*.j2d3.com", self.main)
        self.assertNotIn("*.j2d3.com", self.readme)

    def test_sites_records_are_complete(self) -> None:
        for value in (
            '"custom-domains.chatgpt.site."',
            "_openai-site-verification.${local.portal_hostname}",
            "_cf-custom-hostname.${local.portal_hostname}",
            "openai-site-verification=IqdULfiTB3ESC64Zc45864orM6PGtI2rAaYFdK0SPBQ",
            "9bbfa39a-d1b6-476a-abcf-a41ef74b312f",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.main)

        self.assertEqual(self.main.count('type    = "TXT"'), 2)
        self.assertEqual(self.main.count('type    = "CNAME"'), 1)

    def test_uses_existing_public_zone(self) -> None:
        self.assertIn('data "aws_route53_zone" "public"', self.main)
        self.assertIn("private_zone = false", self.main)
        self.assertIn("allow_overwrite = false", self.main)
        self.assertNotRegex(self.main, r'resource\s+"aws_route53_zone"')

    def test_dns_state_is_separate_and_locked(self) -> None:
        self.assertIn('key          = "environments/dns/terraform.tfstate"', self.versions)
        self.assertIn("encrypt      = true", self.versions)
        self.assertIn("use_lockfile = true", self.versions)

        dev_root = ROOT / "terraform" / "environments" / "dev"
        dev_payload = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(dev_root.glob("*.tf"))
        )
        self.assertNotIn("aws_route53_record", dev_payload)
        self.assertNotIn('key          = "environments/dns/terraform.tfstate"', dev_payload)

    def test_no_automated_terraform_mutation(self) -> None:
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
        )
        self.assertIsNone(
            re.search(
                r"\bterraform(?:\s+-chdir=\S+)?\s+(?:apply|destroy)\b",
                workflows,
                re.IGNORECASE,
            )
        )

    def test_docs_keep_dns_and_tls_evidence_distinct(self) -> None:
        normalized = " ".join(self.readme.split())
        for statement in (
            "Terraform owns Route 53 only",
            "DNS propagation is not certificate evidence",
            "There is no wildcard record",
            "whose SAN covers the exact hostname",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, normalized)


if __name__ == "__main__":
    unittest.main()
