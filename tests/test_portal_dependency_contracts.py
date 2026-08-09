"""Contracts for the portal's temporary Vinext security rollback.

Issue #215 tracks the exit condition for the exact ``vinext`` pin introduced
by #212.  The pin is deliberate: newer Vinext releases currently bring the
unfixed ``image-size`` advisory into the portal dependency graph.  These
checks make an accidental Dependabot or manual upgrade fail loudly until the
exit condition has been reviewed and documented.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "control-plane" / "portal"
PACKAGE = PORTAL / "package.json"
LOCKFILE = PORTAL / "package-lock.json"

EXPECTED_VINEXT = "0.0.45"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PortalVinextRollbackContract(unittest.TestCase):
    def setUp(self) -> None:
        self.package = _load(PACKAGE)
        self.lockfile = _load(LOCKFILE)

    def test_package_declares_the_reviewed_exact_vinext_pin(self) -> None:
        """Do not silently reintroduce the known image-size audit finding."""
        self.assertEqual(
            self.package["devDependencies"].get("vinext"),
            EXPECTED_VINEXT,
            "update the #215 exit-condition evidence before changing Vinext",
        )

    def test_lockfile_root_and_installed_package_match_the_pin(self) -> None:
        root = self.lockfile["packages"][""]
        installed = self.lockfile["packages"]["node_modules/vinext"]
        self.assertEqual(root["devDependencies"].get("vinext"), EXPECTED_VINEXT)
        self.assertEqual(installed["version"], EXPECTED_VINEXT)

    def test_lockfile_does_not_contain_the_unfixed_image_size_package(self) -> None:
        """The current audited graph must stay free of image-size."""
        image_size_paths = [
            path
            for path in self.lockfile["packages"]
            if "node_modules/image-size" in path
        ]
        self.assertEqual(
            image_size_paths,
            [],
            "re-upgrade Vinext only after #215's image-size remediation gate",
        )

    def test_dependabot_ignores_vinext_while_the_rollback_holds(self) -> None:
        """Dependabot must not re-propose the #212 revert (as #214 did).

        The ignore entry and the pin leave together, per #215.
        """
        config = yaml.safe_load(
            (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        )
        portal_blocks = [
            entry
            for entry in config["updates"]
            if entry["package-ecosystem"] == "npm"
            and entry["directory"] == "/control-plane/portal"
        ]
        self.assertEqual(len(portal_blocks), 1)
        ignored = [
            rule["dependency-name"]
            for rule in portal_blocks[0].get("ignore", [])
        ]
        self.assertIn(
            "vinext",
            ignored,
            "restore the dependabot ignore entry, or remove it only with the "
            "#215 re-upgrade",
        )


if __name__ == "__main__":
    unittest.main()
