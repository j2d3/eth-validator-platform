from __future__ import annotations

import json
import re
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path

import yaml

from tools import (
    compare_image_inventories,
    discover_container_images,
    verify_image_scan_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


class ImageInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = discover_container_images.build_inventory()

    def test_expected_application_images_are_source_discovered(self) -> None:
        repositories = {image.repository for image in self.inventory.images}
        self.assertTrue(
            {
                "ethereum/client-go:v1.17.5",
                "sigp/lighthouse:v8.2.1",
                "consensys/web3signer:26.4.2",
                "flyway/flyway:13.0.0-alpine",
                "busybox:1.37.0",
                "ghcr.io/cloudnative-pg/postgresql:18.3-system-trixie",
                "docker.io/grafana/loki",
                "docker.io/grafana/loki-canary",
                "docker.io/grafana/alloy",
                "kindest/node:v1.35.5",
            }.issubset(repositories)
        )

    def test_repository_wide_runtime_sources_are_accounted_for(self) -> None:
        yaml_paths = set(discover_container_images.yaml_paths())
        self.assertIn(Path(".github/workflows/image-security.yaml"), yaml_paths)
        self.assertIn(Path("clusters/local/apps.yaml"), yaml_paths)
        self.assertIn(
            Path("charts/ethereum-node/values-eks-hoodi-storage.yaml"), yaml_paths
        )
        self.assertNotIn(Path("charts/ethereum-node/templates/node.yaml"), yaml_paths)
        self.assertIn(
            Path("charts/ethereum-node/templates/node.yaml"),
            discover_container_images.helm_template_paths(),
        )
        self.assertIn(
            Path("hack/local-cluster.sh"), discover_container_images.shell_paths()
        )
        self.assertFalse(
            any(".terraform" in path.parts for path in yaml_paths),
            "downloaded Terraform modules must not change desired-state image inventory",
        )

        kind = next(
            image
            for image in self.inventory.images
            if image.repository == "kindest/node:v1.35.5"
        )
        self.assertIn("hack/local-cluster.sh:5:shell-image-reference", kind.sources)
        self.assertGreaterEqual(len(self.inventory.scope_exclusions), 3)

    def test_every_scan_subject_is_an_exact_unique_digest(self) -> None:
        images = [image.image for image in self.inventory.images]
        ids = [image.id for image in self.inventory.images]
        self.assertEqual(len(images), len(set(images)))
        self.assertEqual(len(ids), len(set(ids)))
        for image in images:
            self.assertRegex(image, discover_container_images.PINNED_IMAGE_RE)

    def test_helm_tag_digest_is_one_scannable_subject(self) -> None:
        matches = [
            image
            for image in self.inventory.images
            if "aws-load-balancer-controller" in image.image
        ]
        self.assertEqual(len(matches), 1)
        subject = matches[0]
        self.assertEqual(
            subject.image,
            "public.ecr.aws/eks/aws-load-balancer-controller:v3.5.0@"
            "sha256:298acdff5a571731276aaea3d5cc450a264e4ad710a5bddf3e518f68a3f9f6cb",
        )
        self.assertRegex(subject.id, r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
        self.assertFalse(
            any(image.repository == "v3.5.0" for image in self.inventory.images)
        )

    def test_unresolved_flux_and_chart_images_remain_visible(self) -> None:
        unpinned = {
            gap.subject for gap in self.inventory.gaps if gap.kind == "unpinned-image"
        }
        self.assertEqual(
            unpinned,
            {
                "ghcr.io/fluxcd/source-controller:v1.8.5",
                "ghcr.io/fluxcd/kustomize-controller:v1.8.5",
                "ghcr.io/fluxcd/helm-controller:v1.5.5",
                "ghcr.io/fluxcd/notification-controller:v1.8.4",
            },
        )
        chart_gaps = {
            gap.subject
            for gap in self.inventory.gaps
            if gap.kind == "helm-chart-defaults"
        }
        self.assertEqual(
            chart_gaps,
            {
                "alloy@1.11.0",
                "aws-load-balancer-controller@3.5.0",
                "cloudnative-pg@0.29.0",
                "external-secrets@2.8.0",
                "ingress-nginx@4.15.1",
                "kube-prometheus-stack@86.0.0",
                "loki@7.2.0",
            },
        )

    def test_machine_outputs_are_valid_and_stable(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "tools" / "discover_container_images.py"),
                "--format",
                "github-matrix",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        matrix = json.loads(result.stdout)
        self.assertEqual(len(matrix["include"]), len(self.inventory.images))
        for item in matrix["include"]:
            self.assertRegex(item["id"], r"^[a-z0-9-]+$")
            self.assertRegex(item["image"], discover_container_images.PINNED_IMAGE_RE)


class ImageSecurityWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = ROOT / ".github" / "workflows" / "image-security.yaml"
        self.text = self.path.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)

    def test_workflow_has_no_cloud_or_write_authority(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotIn("id-token", self.text)
        self.assertNotIn("AWS_", self.text)
        self.assertNotIn("secrets.", self.text)

    def test_actions_are_sha_pinned_and_results_are_retained(self) -> None:
        action_uses = re.findall(r"^\s*uses:\s*(\S+)", self.text, re.MULTILINE)
        self.assertGreaterEqual(len(action_uses), 4)
        for action in action_uses:
            self.assertRegex(action, r"@[0-9a-f]{40}$")
        self.assertIn("trivy-results.json", self.text)
        self.assertIn("trivy-version.json", self.text)
        self.assertIn("trivy version --cache-dir .cache/trivy --format json", self.text)
        self.assertIn("image-inventory.json", self.text)
        self.assertIn("retention-days: 14", self.text)

    def test_initial_slice_is_evidence_not_a_false_promotion_gate(self) -> None:
        self.assertIn('exit-code: "0"', self.text)
        self.assertIn("ignore-unfixed: false", self.text)
        self.assertIn("UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL", self.text)
        self.assertIn("workflow_dispatch", self.text)
        self.assertIn("schedule", self.text)

    def test_image_source_changes_trigger_a_new_inventory(self) -> None:
        for path_filter in (
            '"**/*.yaml"',
            '"**/*.yml"',
            '"**/*.sh"',
            '"**/Dockerfile*"',
            "requirements-dev.txt",
            "tools/compare_image_inventories.py",
            "tools/verify_image_scan_evidence.py",
        ):
            self.assertIn(path_filter, self.text)

    def test_report_binding_runs_before_evidence_upload(self) -> None:
        verifier = "python3 tools/verify_image_scan_evidence.py"
        upload = "name: Retain scanner evidence"
        self.assertIn(verifier, self.text)
        self.assertLess(self.text.index(verifier), self.text.index(upload))
        for contract in ("ArtifactType", "ArtifactName", "RepoDigests"):
            self.assertIn(
                contract,
                (ROOT / "tools" / "verify_image_scan_evidence.py").read_text(
                    encoding="utf-8"
                ),
            )

    def test_unchanged_pull_request_inventory_skips_only_the_scan_matrix(self) -> None:
        inventory = self.workflow["jobs"]["inventory"]
        checkout = inventory["steps"][0]
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        self.assertIn("scan_required", inventory["outputs"])

        scan = self.workflow["jobs"]["scan"]
        self.assertEqual(
            scan["if"], "needs.inventory.outputs.scan_required == 'true'"
        )
        decision = self.workflow["jobs"]["evidence-decision"]
        self.assertEqual(decision["if"], "always()")
        self.assertEqual(decision["needs"], ["inventory", "scan"])

        self.assertIn("base-image-inventory.json", self.text)
        self.assertIn("tools/compare_image_inventories.py", self.text)
        self.assertIn('GITHUB_EVENT_NAME" != "pull_request', self.text)
        self.assertIn('SCAN_RESULT" == "skipped', self.text)
        self.assertIn("Existing evidence applies only to the unchanged exact digests", self.text)


class ImageInventoryComparisonTests(unittest.TestCase):
    def inventory(
        self,
        *,
        image: str = "example.invalid/client:v1@sha256:" + "a" * 64,
        sources: list[str] | None = None,
        gaps: list[dict] | None = None,
    ) -> dict:
        return {
            "schemaVersion": 1,
            "images": [
                {
                    "id": "example-client",
                    "image": image,
                    "repository": "example.invalid/client:v1",
                    "digest": "sha256:" + "a" * 64,
                    "sources": sources or ["charts/example/values.yaml"],
                }
            ],
            "coverageGaps": gaps or [],
            "scopeExclusions": [
                {"paths": ["docs/**"], "reason": "not a runtime source"}
            ],
        }

    def test_same_exact_subjects_reuse_evidence_when_only_sources_change(self) -> None:
        base = self.inventory()
        current = self.inventory(sources=["platform/example/release.yaml"])
        result = compare_image_inventories.compare(base, current)
        self.assertFalse(result["scanRequired"])

    def test_digest_or_coverage_boundary_change_requires_a_scan(self) -> None:
        base = self.inventory()
        digest_change = self.inventory(
            image="example.invalid/client:v1@sha256:" + "b" * 64
        )
        gap_change = self.inventory(
            gaps=[
                {
                    "kind": "helm-chart",
                    "subject": "example/chart@1.0.0",
                    "source": "platform/example.yaml",
                    "reason": "transitive image unresolved",
                }
            ]
        )

        self.assertTrue(
            compare_image_inventories.compare(base, digest_change)["scanRequired"]
        )
        self.assertTrue(
            compare_image_inventories.compare(base, gap_change)["scanRequired"]
        )

    def test_malformed_inventory_fails_closed(self) -> None:
        malformed = self.inventory()
        malformed["images"] = [{"id": "missing-image"}]
        with self.assertRaises(compare_image_inventories.InventoryComparisonError):
            compare_image_inventories.compare(self.inventory(), malformed)


class ImageScanEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = (
            "example.invalid/client:v1@sha256:"
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        self.report = {
            "ArtifactType": "container_image",
            "ArtifactName": self.image,
            "CreatedAt": "2026-08-03T20:00:00Z",
            "Metadata": {
                "RepoDigests": [
                    "example.invalid/client@sha256:"
                    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                ]
            },
            "Trivy": {"Version": "0.73.0"},
        }
        self.version = {
            "Version": "0.73.0",
            "VulnerabilityDB": {
                "Version": 2,
                "UpdatedAt": "2026-08-03T14:00:00Z",
            },
        }

    def test_exact_report_binding_is_accepted(self) -> None:
        verified = verify_image_scan_evidence.verify_scan_evidence(
            self.image,
            self.report,
            self.version,
        )
        self.assertEqual(verified["artifactName"], self.image)
        self.assertEqual(
            verified["digest"],
            "sha256:"
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )

    def test_non_image_report_is_rejected(self) -> None:
        report = deepcopy(self.report)
        report["ArtifactType"] = "filesystem"
        with self.assertRaises(verify_image_scan_evidence.EvidenceError):
            verify_image_scan_evidence.verify_scan_evidence(
                self.image, report, self.version
            )

    def test_mismatched_artifact_name_is_rejected(self) -> None:
        report = deepcopy(self.report)
        report["ArtifactName"] = "example.invalid/other@sha256:" + "a" * 64
        with self.assertRaises(verify_image_scan_evidence.EvidenceError):
            verify_image_scan_evidence.verify_scan_evidence(
                self.image, report, self.version
            )

    def test_mismatched_repository_digest_is_rejected(self) -> None:
        report = deepcopy(self.report)
        report["Metadata"]["RepoDigests"] = [
            "example.invalid/client@sha256:" + "a" * 64
        ]
        with self.assertRaises(verify_image_scan_evidence.EvidenceError):
            verify_image_scan_evidence.verify_scan_evidence(
                self.image, report, self.version
            )


if __name__ == "__main__":
    unittest.main()
