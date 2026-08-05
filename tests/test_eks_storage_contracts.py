"""Contracts for the declarative EKS application storage adapter.

These assert the desired shape of a live manifest and values profile. Runtime
evidence separately records a reconciled StorageClass and bound EBS claims;
these tests do not themselves prove provisioning, attachment, expansion,
snapshot recovery, or cost.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEV_CONFIGS = ROOT / "platform" / "infrastructure" / "configs" / "dev"
STORAGE_CLASS_FILE = DEV_CONFIGS / "ebs-gp3-storage-class.yaml"
DEV_README = DEV_CONFIGS / "README.md"
CHART = ROOT / "charts" / "ethereum-node"
EKS_STORAGE_VALUES = CHART / "values-eks-hoodi-storage.yaml"
EKS_EPHEMERY_VALUES = CHART / "values-eks-ephemery.yaml"
CLUSTERS = ROOT / "clusters"

# The historical claim the directory README made before clusters/dev existed.
# It is asserted against the filesystem so reintroducing it cannot quietly make
# a registered adapter sound unreconciled.
UNRECONCILED_CLAIM = "no Flux Kustomization reconciles this directory"
DEV_CONFIGS_PATH = "platform/infrastructure/configs/dev"

BINARY_SUFFIXES = {"Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}


def load_documents(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [document for document in yaml.safe_load_all(stream) if document]


def to_bytes(quantity: str) -> int:
    """Parse the binary-suffixed sizes the chart schema allows."""

    suffix = quantity[-2:]
    return int(quantity[:-2]) * BINARY_SUFFIXES[suffix]


class EksStorageClassTests(unittest.TestCase):
    def setUp(self) -> None:
        documents = load_documents(STORAGE_CLASS_FILE)
        self.assertEqual(len(documents), 1, "the adapter declares exactly one StorageClass")
        self.storage_class = documents[0]

    def test_uses_the_ebs_csi_driver_with_encrypted_gp3_ext4(self) -> None:
        self.assertEqual(self.storage_class["kind"], "StorageClass")
        self.assertEqual(self.storage_class["provisioner"], "ebs.csi.aws.com")

        parameters = self.storage_class["parameters"]
        self.assertEqual(parameters["type"], "gp3")
        self.assertEqual(parameters["encrypted"], "true")
        self.assertEqual(parameters["csi.storage.k8s.io/fstype"], "ext4")

    def test_binds_late_expands_and_releases_reproducible_chain_data(self) -> None:
        """WaitForFirstConsumer keeps the volume in the zone that runs the pair.

        Delete is correct for these claims specifically because execution and
        consensus databases are reproducible by resync, and neither signing keys
        nor slashing-protection history is ever stored on them.
        """

        self.assertEqual(self.storage_class["volumeBindingMode"], "WaitForFirstConsumer")
        self.assertTrue(self.storage_class["allowVolumeExpansion"])
        self.assertEqual(self.storage_class["reclaimPolicy"], "Delete")

    def test_is_not_a_cluster_wide_default(self) -> None:
        """A workload that forgets to name a class must fail visibly.

        Defaulting would make the encrypted gp3 class reachable by omission,
        which is the same accident as inheriting gp2 — just in the other
        direction, and undetectable in review.
        """

        annotations = self.storage_class["metadata"].get("annotations", {})
        self.assertNotIn("storageclass.kubernetes.io/is-default-class", annotations)

    def test_requests_baseline_gp3_performance_only(self) -> None:
        """gp3 includes 3,000 IOPS and 125 MiB/s in its per-GiB price.

        Provisioning beyond baseline is billed separately, so it belongs behind
        measured queue depth, latency, or sync-rate evidence. This test is the
        tripwire that forces that conversation.
        """

        parameters = self.storage_class["parameters"]
        for provisioned in ("iops", "throughput"):
            with self.subTest(parameter=provisioned):
                self.assertNotIn(provisioned, parameters)

    def test_does_not_reintroduce_a_legacy_or_local_class(self) -> None:
        name = self.storage_class["metadata"]["name"]
        self.assertNotIn(name, {"gp2", "standard"})
        self.assertNotIn(self.storage_class["parameters"]["type"], {"gp2", "io1", "io2", "st1", "sc1"})

    def test_kustomization_includes_the_storage_class(self) -> None:
        kustomization = load_documents(DEV_CONFIGS / "kustomization.yaml")[0]
        self.assertIn(STORAGE_CLASS_FILE.name, kustomization["resources"])

    def test_offline_validation_covers_the_declared_directory(self) -> None:
        """Offline rendering remains the standing guard for later edits."""

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(f"kubectl kustomize {DEV_CONFIGS_PATH}", makefile)


class EksStorageProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = yaml.safe_load(EKS_STORAGE_VALUES.read_text(encoding="utf-8"))
        self.ephemery_profile = yaml.safe_load(EKS_EPHEMERY_VALUES.read_text(encoding="utf-8"))
        self.chart_defaults = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
        self.declared_class = load_documents(STORAGE_CLASS_FILE)[0]["metadata"]["name"]

    def test_profile_names_the_declared_storage_class(self) -> None:
        self.assertEqual(self.profile["storageClassName"], self.declared_class)
        self.assertEqual(self.ephemery_profile["storageClassName"], self.declared_class)

    def test_profile_cannot_inherit_gp2_or_the_local_class(self) -> None:
        self.assertNotIn(self.profile["storageClassName"], {"gp2", "standard", ""})

    def test_chart_default_stays_local_so_the_eks_class_is_opt_in(self) -> None:
        """The EKS class must not become a chart-wide default.

        `standard` exists in kind and not on EKS; the encrypted gp3 class exists
        on EKS and not in kind. Each environment names its own, and neither
        inherits the other's.
        """

        self.assertNotEqual(self.chart_defaults["storageClassName"], self.declared_class)

    def test_hoodi_sizes_are_the_proposed_first_sync_hypothesis(self) -> None:
        self.assertEqual(
            self.profile["storage"],
            {"execution": "200Gi", "consensus": "50Gi", "validator": "5Gi"},
        )

    def test_ephemery_sizes_are_reset_aware_and_cheaper_than_hoodi(self) -> None:
        self.assertEqual(
            self.ephemery_profile["storage"],
            {"execution": "50Gi", "consensus": "20Gi", "validator": "5Gi"},
        )
        for claim in ("execution", "consensus"):
            with self.subTest(claim=claim):
                self.assertLess(
                    to_bytes(self.ephemery_profile["storage"][claim]),
                    to_bytes(self.profile["storage"][claim]),
                )

    def test_sizes_are_expandable_from_the_local_defaults(self) -> None:
        """Volume expansion grows a claim; nothing shrinks one.

        An EKS size below the chart default would be a size the profile could
        never reach by expanding, which is a design error rather than a saving.
        """

        for claim, default in self.chart_defaults["storage"].items():
            with self.subTest(claim=claim):
                self.assertGreaterEqual(to_bytes(self.profile["storage"][claim]), to_bytes(default))
                self.assertGreaterEqual(
                    to_bytes(self.ephemery_profile["storage"][claim]),
                    to_bytes(default),
                )

    def test_chart_schema_requires_an_explicit_class_and_sizes(self) -> None:
        schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
        self.assertIn("storageClassName", schema["required"])
        self.assertIn("storage", schema["required"])
        self.assertEqual(
            sorted(schema["properties"]["storage"]["required"]),
            ["consensus", "execution", "validator"],
        )


class EksAdapterTruthfulnessTests(unittest.TestCase):
    def test_readme_reconciliation_claim_matches_the_flux_entrypoints(self) -> None:
        """The README must track whether Flux registers this directory."""

        readme = DEV_README.read_text(encoding="utf-8")
        registered = any(
            DEV_CONFIGS_PATH in path.read_text(encoding="utf-8")
            for path in sorted(CLUSTERS.rglob("*.yaml"))
        )

        if registered:
            self.assertNotIn(
                UNRECONCILED_CLAIM,
                readme,
                "a Flux Kustomization now reconciles this directory; the README must stop saying otherwise",
            )
        else:
            self.assertIn(
                UNRECONCILED_CLAIM,
                readme,
                "no Flux entrypoint reconciles this directory; the README must say so",
            )

    def test_readme_records_live_storage_without_overclaiming_recovery(self) -> None:
        readme = " ".join(DEV_README.read_text(encoding="utf-8").split())
        self.assertNotIn("No AWS resource has been created", readme)
        self.assertIn("22 bound Ethereum claims", readme)
        self.assertIn("does not prove online expansion", readme)
        self.assertIn("snapshot restore", readme)

    def test_sizes_are_documented_as_a_hypothesis_not_as_guidance(self) -> None:
        readme = " ".join(DEV_README.read_text(encoding="utf-8").split())
        self.assertIn("hypothesis", readme)
        self.assertIn("not production guidance", readme)

    def test_archive_economics_keep_replaceable_data_separate_from_safety_data(self) -> None:
        readme = " ".join(DEV_README.read_text(encoding="utf-8").split())
        self.assertIn("Retain, snapshot, or resync", readme)
        self.assertIn("650 GiB provisioned", readme)
        self.assertIn("$52/month", readme)
        self.assertIn("Snapshot billing follows written blocks", readme)
        self.assertIn("Validator keys remain in Secrets Manager", readme)
        self.assertIn("slashing history remains in PostgreSQL", readme)
        self.assertIn("must be read again when the decision is made", readme)


if __name__ == "__main__":
    unittest.main()
