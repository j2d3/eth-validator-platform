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


def image_ref(letter: str) -> str:
    return f"registry.invalid/{letter}@sha256:{letter * 64}"


def decision(image: str, critical: tuple[int, int], high: tuple[int, int]):
    def block(values: tuple[int, int]):
        available, unavailable = values
        return {
            "available": available,
            "unavailable": unavailable,
            "total": available + unavailable,
        }

    return {
        "schemaVersion": 1,
        # Match the production verifier: both evidence and inventory retain
        # the explicit algorithm prefix.
        "subject": {
            "image": image,
            "digest": "sha256:" + image.rpartition("@sha256:")[2],
        },
        "evaluatedAt": "2026-08-05T13:42:11Z",
        "counts": {"critical": block(critical), "high": block(high)},
        "unexceptedCounts": {"critical": block(critical), "high": block(high)},
        "decision": {"mode": "evidence-only", "promotionGate": False},
    }


def inventory(images: list[str], gaps: int = 0):
    return {
        "schemaVersion": 1,
        "images": [
            {
                "id": f"subject-{index}",
                "image": image,
                "repository": image.partition("@")[0],
                "digest": "sha256:" + image.rpartition("@sha256:")[2],
                "sources": ["platform/example.yaml:1:$.image"],
            }
            for index, image in enumerate(images)
        ],
        "coverageGaps": [
            {
                "kind": "unpinned-image",
                "subject": f"example:tag-{index}",
                "source": "platform/example.yaml",
                "reason": "desired state names a tag rather than an exact digest",
            }
            for index in range(gaps)
        ],
        "scopeExclusions": [],
    }


def sbom_subject(image: str):
    return {
        "schemaVersion": 1,
        "image": image,
        "digest": "sha256:" + image.rpartition("@sha256:")[2],
        "format": "CycloneDX",
        "specVersion": "1.7",
        "generatedAt": "2026-08-05T13:42:11Z",
        "componentCount": 3,
        "scannerVersion": "0.73.0",
        "provenance": {"sourceSha": "c" * 40},
    }


class AggregateImageScanDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name) / "decisions"
        self.root.mkdir()
        self.inventory_path = Path(self._directory.name) / "image-inventory.json"

    def write_decision(self, name: str, value: dict) -> None:
        directory = self.root / f"image-scan-{name}"
        directory.mkdir()
        (directory / "image-scan-decision.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        (directory / "sbom-subject.json").write_text(
            json.dumps(sbom_subject(value["subject"]["image"])), encoding="utf-8"
        )

    def write_inventory(self, value: dict) -> Path:
        self.inventory_path.write_text(json.dumps(value), encoding="utf-8")
        return self.inventory_path

    def aggregate(self) -> dict:
        return aggregate_module.aggregate(self.root, self.inventory_path)

    def test_aggregates_counts_and_coverage_for_the_exact_inventory(self) -> None:
        self.write_inventory(inventory([image_ref("a"), image_ref("b")], gaps=11))
        self.write_decision("one", decision(image_ref("a"), (1, 2), (3, 4)))
        self.write_decision("two", decision(image_ref("b"), (5, 6), (7, 8)))

        result = self.aggregate()

        self.assertEqual(result["exactSubjects"], 2)
        self.assertEqual(result["scannedSubjects"], 2)
        self.assertEqual(result["sbomSubjects"], 2)
        self.assertEqual(result["coverageGaps"], 11)
        self.assertEqual(
            result["counts"]["critical"], {"available": 6, "total": 14, "unavailable": 8}
        )
        self.assertEqual(
            result["counts"]["high"], {"available": 10, "total": 22, "unavailable": 12}
        )
        self.assertFalse(result["decision"]["promotionGate"])

        outputs = aggregate_module.github_outputs(result)
        for expected in (
            "available=true\n",
            "exact_subjects=2\n",
            "scanned_subjects=2\n",
            "sbom_subjects=2\n",
            "coverage_gaps=11\n",
            "critical_total=14\n",
            "high_available=10\n",
        ):
            self.assertIn(expected, outputs)
        self.assertNotIn("images=", outputs)

    def test_rejects_a_discovered_subject_without_evidence(self) -> None:
        self.write_inventory(inventory([image_ref("a"), image_ref("b")]))
        self.write_decision("one", decision(image_ref("a"), (0, 0), (0, 0)))

        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "no evidence decision"
        ) as raised:
            self.aggregate()
        self.assertIn(image_ref("b"), str(raised.exception))

    def test_rejects_evidence_for_an_undiscovered_subject(self) -> None:
        self.write_inventory(inventory([image_ref("a")]))
        self.write_decision("one", decision(image_ref("a"), (0, 0), (0, 0)))
        self.write_decision("two", decision(image_ref("b"), (9, 9), (9, 9)))

        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "not in the discovered inventory"
        ) as raised:
            self.aggregate()
        self.assertIn(image_ref("b"), str(raised.exception))

    def test_rejects_duplicate_decision_subjects(self) -> None:
        self.write_inventory(inventory([image_ref("a")]))
        value = decision(image_ref("a"), (0, 0), (0, 0))
        self.write_decision("one", value)
        self.write_decision("two", value)

        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "duplicate image subject"
        ):
            self.aggregate()

    def test_rejects_duplicate_inventory_subjects(self) -> None:
        self.write_inventory(inventory([image_ref("a"), image_ref("a")]))
        self.write_decision("one", decision(image_ref("a"), (0, 0), (0, 0)))

        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "duplicate image subject"
        ):
            self.aggregate()

    def test_rejects_a_decision_digest_that_contradicts_its_image(self) -> None:
        self.write_inventory(inventory([image_ref("a")]))
        value = decision(image_ref("a"), (0, 0), (0, 0))
        value["subject"]["digest"] = "sha256:" + "b" * 64
        self.write_decision("one", value)

        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "digest does not match"
        ):
            self.aggregate()

    def test_rejects_an_unpinned_inventory_subject(self) -> None:
        document = inventory([image_ref("a")])
        document["images"][0]["image"] = "registry.invalid/a:v1"
        self.write_inventory(document)
        self.write_decision("one", decision(image_ref("a"), (0, 0), (0, 0)))

        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "not pinned by sha256 digest"
        ):
            self.aggregate()

    def test_rejects_a_malformed_inventory_document(self) -> None:
        for mutate, message in (
            (lambda value: value.update(schemaVersion=2), "schemaVersion must be 1"),
            (lambda value: value.update(images=[]), "at least one discovered image"),
            (lambda value: value.update(images="all"), "at least one discovered image"),
            (lambda value: value.update(coverageGaps=11), "coverageGaps must be"),
            (lambda value: value.update(coverageGaps=["gap"]), "coverageGaps must be"),
        ):
            with self.subTest(message=message):
                document = inventory([image_ref("a")], gaps=1)
                mutate(document)
                self.write_inventory(document)
                with self.assertRaisesRegex(aggregate_module.AggregateError, message):
                    aggregate_module.load_inventory(self.inventory_path)

    def test_rejects_a_missing_inventory_artifact(self) -> None:
        self.write_decision("one", decision(image_ref("a"), (0, 0), (0, 0)))
        with self.assertRaisesRegex(aggregate_module.AggregateError, "cannot read"):
            self.aggregate()

    def test_rejects_missing_or_wrong_sbom_subject_evidence(self) -> None:
        self.write_inventory(inventory([image_ref("a")]))
        self.write_decision("one", decision(image_ref("a"), (0, 0), (0, 0)))
        sbom_path = self.root / "image-scan-one" / "sbom-subject.json"
        sbom_path.unlink()
        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "no verified SBOM subject"
        ):
            self.aggregate()

        sbom_path.write_text(
            json.dumps(sbom_subject(image_ref("b"))), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "no verified SBOM"
        ):
            self.aggregate()

    def test_rejects_an_enabled_promotion_gate(self) -> None:
        self.write_inventory(inventory([image_ref("a")]))
        value = decision(image_ref("a"), (0, 0), (0, 0))
        value["decision"]["promotionGate"] = True
        self.write_decision("one", value)

        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "promotion gate"
        ):
            self.aggregate()

    def test_rejects_an_empty_decision_set(self) -> None:
        self.write_inventory(inventory([image_ref("a")]))
        with self.assertRaisesRegex(
            aggregate_module.AggregateError, "no image-scan decision artifacts"
        ):
            self.aggregate()


if __name__ == "__main__":
    unittest.main()
