import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "aggregate_image_scan_decisions",
    ROOT / "tools" / "aggregate_image_scan_decisions.py",
)
assert SPEC and SPEC.loader
aggregate_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aggregate_module)


def decision(image: str, digest: str, critical: tuple[int, int], high: tuple[int, int]):
    def block(values: tuple[int, int]):
        available, unavailable = values
        return {
            "available": available,
            "unavailable": unavailable,
            "total": available + unavailable,
        }

    return {
        "schemaVersion": 1,
        "subject": {"image": image, "digest": digest},
        "evaluatedAt": "2026-08-05T13:42:11Z",
        "counts": {"critical": block(critical), "high": block(high)},
        "unexceptedCounts": {"critical": block(critical), "high": block(high)},
        "decision": {"mode": "evidence-only", "promotionGate": False},
    }


class AggregateImageScanDecisionTests(unittest.TestCase):
    def write(self, root: Path, name: str, value: dict) -> None:
        directory = root / f"image-scan-{name}"
        directory.mkdir()
        (directory / "image-scan-decision.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def test_aggregates_occurrence_counts_and_github_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "one", decision("one@sha256:" + "a" * 64, "sha256:" + "a" * 64, (1, 2), (3, 4)))
            self.write(root, "two", decision("two@sha256:" + "b" * 64, "sha256:" + "b" * 64, (5, 6), (7, 8)))
            result = aggregate_module.aggregate(root)

        self.assertEqual(result["images"], 2)
        self.assertEqual(result["counts"]["critical"], {"available": 6, "total": 14, "unavailable": 8})
        self.assertEqual(result["counts"]["high"], {"available": 10, "total": 22, "unavailable": 12})
        self.assertFalse(result["decision"]["promotionGate"])
        self.assertIn("critical_total=14\n", aggregate_module.github_outputs(result))

    def test_rejects_duplicate_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = decision("one@sha256:" + "a" * 64, "sha256:" + "a" * 64, (0, 0), (0, 0))
            self.write(root, "one", value)
            self.write(root, "two", value)
            with self.assertRaisesRegex(aggregate_module.AggregateError, "duplicate image subject"):
                aggregate_module.aggregate(root)

    def test_rejects_an_enabled_promotion_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = decision("one@sha256:" + "a" * 64, "sha256:" + "a" * 64, (0, 0), (0, 0))
            value["decision"]["promotionGate"] = True
            self.write(root, "one", value)
            with self.assertRaisesRegex(aggregate_module.AggregateError, "promotion gate"):
                aggregate_module.aggregate(root)


def inventory_of(
    subjects: list[tuple[str, str]],
    *,
    gaps: list[dict] | None = None,
) -> dict:
    return {
        "schemaVersion": 1,
        "images": [
            {"id": image.split("@")[0], "image": image, "digest": digest}
            for image, digest in subjects
        ],
        "coverageGaps": gaps or [],
        "scopeExclusions": [],
    }


class AggregateImageScanCoverageTests(unittest.TestCase):
    """Bind aggregation to the discovered inventory (issue #43 slice)."""

    def write(self, root: Path, name: str, value: dict) -> None:
        directory = root / f"image-scan-{name}"
        directory.mkdir()
        (directory / "image-scan-decision.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def test_publishes_exact_scanned_and_coverage_gap_counts(self) -> None:
        one = ("one@sha256:" + "a" * 64, "sha256:" + "a" * 64)
        two = ("two@sha256:" + "b" * 64, "sha256:" + "b" * 64)
        gaps = [
            {"kind": "helm-chart-defaults", "subject": "x@1", "source": "a.yaml",
             "reason": "unresolved"},
            {"kind": "helm-chart-defaults", "subject": "y@2", "source": "b.yaml",
             "reason": "unresolved"},
            {"kind": "helm-chart-values", "subject": "z@3", "source": "c.yaml",
             "reason": "unresolved"},
        ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "one", decision(*one, (0, 0), (0, 0)))
            self.write(root, "two", decision(*two, (0, 0), (0, 0)))
            result = aggregate_module.aggregate(
                root, inventory=inventory_of([one, two], gaps=gaps)
            )

        self.assertEqual(result["exactSubjects"], {"scanned": 2, "expected": 2})
        self.assertEqual(
            result["coverageGaps"],
            {"count": 3, "kinds": {"helm-chart-defaults": 2, "helm-chart-values": 1}},
        )
        outputs = aggregate_module.github_outputs(result)
        self.assertIn("exact_scanned=2\n", outputs)
        self.assertIn("exact_expected=2\n", outputs)
        self.assertIn("coverage_gaps=3\n", outputs)

    def test_rejects_missing_decision_for_inventory_subject(self) -> None:
        one = ("one@sha256:" + "a" * 64, "sha256:" + "a" * 64)
        two = ("two@sha256:" + "b" * 64, "sha256:" + "b" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "one", decision(*one, (0, 0), (0, 0)))
            with self.assertRaisesRegex(
                aggregate_module.AggregateError,
                "missing image-scan decision.*two@",
            ):
                aggregate_module.aggregate(
                    root, inventory=inventory_of([one, two])
                )

    def test_rejects_decision_not_in_inventory(self) -> None:
        one = ("one@sha256:" + "a" * 64, "sha256:" + "a" * 64)
        two = ("two@sha256:" + "b" * 64, "sha256:" + "b" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "one", decision(*one, (0, 0), (0, 0)))
            self.write(root, "two", decision(*two, (0, 0), (0, 0)))
            with self.assertRaisesRegex(
                aggregate_module.AggregateError,
                "unexpected image-scan decision.*two@",
            ):
                aggregate_module.aggregate(
                    root, inventory=inventory_of([one])
                )

    def test_rejects_malformed_inventory(self) -> None:
        one = ("one@sha256:" + "a" * 64, "sha256:" + "a" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "one", decision(*one, (0, 0), (0, 0)))
            broken = inventory_of([one])
            broken["images"] = [{"id": "no-image-field"}]
            with self.assertRaisesRegex(
                aggregate_module.AggregateError,
                "inventory image is missing string image/digest",
            ):
                aggregate_module.aggregate(root, inventory=broken)

    def test_load_inventory_rejects_wrong_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps({"schemaVersion": 2, "images": [],
                                        "coverageGaps": []}), encoding="utf-8")
            with self.assertRaisesRegex(
                aggregate_module.AggregateError, "not a schemaVersion 1"
            ):
                aggregate_module.load_inventory(path)


if __name__ == "__main__":
    unittest.main()
