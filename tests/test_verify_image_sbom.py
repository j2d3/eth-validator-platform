import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_image_sbom", ROOT / "tools" / "verify_image_sbom.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

DIGEST = "sha256:" + "a" * 64
IMAGE = f"registry.invalid/client:v1@{DIGEST}"


def scan_subject() -> dict:
    return {
        "schemaVersion": 1,
        "image": IMAGE,
        "verifiedReport": {
            "artifactName": IMAGE,
            "digest": DIGEST,
            "scannerVersion": "0.73.0",
        },
        "provenance": {
            "sourceSha": "b" * 40,
            "checkoutSha": "b" * 40,
            "workflowRunId": "123",
            "workflowRunAttempt": "1",
            "event": "push",
        },
    }


def sbom() -> dict:
    purl = f"pkg:oci/client@{DIGEST}?repository_url=registry.invalid%2Fclient"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "timestamp": "2026-08-05T14:54:59+00:00",
            "component": {
                "type": "container",
                "name": IMAGE,
                "bom-ref": purl,
                "purl": purl,
                "properties": [
                    {
                        "name": "aquasecurity:trivy:RepoDigest",
                        "value": f"registry.invalid/client@{DIGEST}",
                    }
                ],
            },
            "tools": {
                "components": [
                    {"type": "application", "name": "trivy", "version": "0.73.0"}
                ]
            },
        },
        "components": [{"type": "library", "name": "example"}],
    }


class VerifyImageSbomTests(unittest.TestCase):
    def test_binds_cyclonedx_to_the_verified_exact_subject(self) -> None:
        result = module.verify_sbom(sbom(), scan_subject())
        self.assertEqual(result["image"], IMAGE)
        self.assertEqual(result["digest"], DIGEST)
        self.assertEqual(result["format"], "CycloneDX")
        self.assertEqual(result["specVersion"], "1.7")
        self.assertEqual(result["componentCount"], 1)
        self.assertEqual(result["generatedAt"], "2026-08-05T14:54:59Z")
        self.assertEqual(result["provenance"]["sourceSha"], "b" * 40)

    def test_rejects_wrong_image_digest_and_scanner_identity(self) -> None:
        cases = []
        wrong_name = sbom()
        wrong_name["metadata"]["component"]["name"] = "registry.invalid/other:v1@" + DIGEST
        cases.append((wrong_name, scan_subject(), "component name"))

        wrong_purl = sbom()
        wrong_purl["metadata"]["component"]["purl"] = "pkg:oci/client@sha256:" + "c" * 64
        cases.append((wrong_purl, scan_subject(), "purl"))

        missing_repo_digest = sbom()
        missing_repo_digest["metadata"]["component"]["properties"] = []
        cases.append((missing_repo_digest, scan_subject(), "repository digest"))

        wrong_tool = sbom()
        wrong_tool["metadata"]["tools"]["components"][0]["version"] = "0.72.0"
        cases.append((wrong_tool, scan_subject(), "Trivy identity"))

        mismatched_subject = scan_subject()
        mismatched_subject["verifiedReport"]["digest"] = "sha256:" + "d" * 64
        cases.append((sbom(), mismatched_subject, "inconsistent"))

        for document, subject, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(module.SbomError, message):
                    module.verify_sbom(document, subject)

    def test_cli_writes_only_public_safe_binding_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sbom_path = root / "image-sbom.cdx.json"
            subject_path = root / "scan-subject.json"
            output_path = root / "sbom-subject.json"
            sbom_path.write_text(json.dumps(sbom()), encoding="utf-8")
            subject_path.write_text(json.dumps(scan_subject()), encoding="utf-8")

            original_argv = module.sys.argv
            self.addCleanup(setattr, module.sys, "argv", original_argv)
            module.sys.argv = [
                "verify_image_sbom.py",
                "--sbom",
                str(sbom_path),
                "--scan-subject",
                str(subject_path),
                "--output",
                str(output_path),
            ]
            self.assertEqual(module.main(), 0)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["digest"], DIGEST)
            self.assertNotIn("components", result)


if __name__ == "__main__":
    unittest.main()
