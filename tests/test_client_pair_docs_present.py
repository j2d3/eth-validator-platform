"""Contracts on the ServiceProfile set.

- Every declared ServiceProfile has a matching client-pair doc (prevents
  the "pair added, docs page never written" drift; a pair is not finished
  until docs/client-pairs/<execution>-<consensus>.md exists).
- ServiceProfiles with no validator-client chart adapter must not
  authorize signing (guards against silently reopening an unrenderable
  path in the catalog validator).
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "applications" / "profiles"
DOCS = ROOT / "docs" / "client-pairs"

# Consensus clients for which charts/ethereum-node/templates/
# validator-client.yaml cannot render. Any ServiceProfile that pairs with
# one of these MUST have `signingAllowed: false` until a corresponding VC
# adapter lands (Teku's arrived in #132; Nimbus and Prysm do not have
# ones yet).
CONSENSUS_CLIENTS_WITHOUT_VALIDATOR_ADAPTER = {"nimbus", "prysm"}


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

    def test_no_signing_allowed_for_consensus_clients_without_validator_adapter(
        self,
    ) -> None:
        violations: list[str] = []
        for profile_path in sorted(PROFILES.glob("dedicated-*.yaml")):
            document = yaml.safe_load(profile_path.read_text())
            spec = document.get("spec", {})
            consensus = spec.get("consensusClient")
            if consensus not in CONSENSUS_CLIENTS_WITHOUT_VALIDATOR_ADAPTER:
                continue
            if spec.get("signingAllowed") is not False:
                violations.append(
                    f"{profile_path.name}: consensusClient={consensus!r} has no "
                    "validator-client chart adapter; signingAllowed must be false "
                    "until a VC adapter lands, otherwise the catalog validator "
                    "silently authorizes an unrenderable signing path."
                )
        self.assertEqual(violations, [], msg="\n  " + "\n  ".join(violations))
