"""Contract: every declared ServiceProfile has a matching client-pair doc.

Prevents the "pair added, docs page never written" drift. A pair is not
finished until docs/client-pairs/<execution>-<consensus>.md exists.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "applications" / "profiles"
DOCS = ROOT / "docs" / "client-pairs"


class ClientPairDocsPresentTests(unittest.TestCase):
    def test_every_service_profile_has_a_client_pair_doc(self) -> None:
        missing: list[str] = []
        for profile_path in sorted(PROFILES.glob("dedicated-*.yaml")):
            document = yaml.safe_load(profile_path.read_text())
            spec = document.get("spec", {})
            execution = spec.get("executionClient")
            consensus = spec.get("consensusClient")
            self.assertIsNotNone(execution, msg=f"{profile_path.name}: missing executionClient")
            self.assertIsNotNone(consensus, msg=f"{profile_path.name}: missing consensusClient")
            expected = DOCS / f"{execution}-{consensus}.md"
            if not expected.exists():
                missing.append(
                    f"{profile_path.name} -> {expected.relative_to(ROOT)} (missing)"
                )
        self.assertEqual(
            missing,
            [],
            msg=(
                "Every ServiceProfile must have a matching client-pair profile "
                "page. Missing:\n  " + "\n  ".join(missing)
            ),
        )
