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


if __name__ == "__main__":
    unittest.main()
