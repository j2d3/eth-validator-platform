"""The telemetry label contract, enforced offline.

Identity must survive the whole path: the pair chart attaches it at scrape
time, every recording rule retains it through aggregation, kube-state-metrics
exposes exactly the object labels needed to join Kubernetes resource metrics
back to an assignment, and the dashboards join on real resource keys. Each of
those links is asserted here so a break shows up in CI rather than as an empty
panel someone mistakes for missing telemetry.

Runtime behaviour is separate evidence and is not asserted.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHART = REPOSITORY_ROOT / "charts" / "ethereum-node"
CONTROLLERS = REPOSITORY_ROOT / "platform" / "infrastructure" / "controllers"
LOCAL_APPS = REPOSITORY_ROOT / "platform" / "apps" / "local"

# Attached at scrape time and retained through every aggregation.
TELEMETRY_LABELS = (
    "cluster",
    "environment",
    "lifecycle_state",
    "network",
    "network_profile",
    "network_generation",
    "network_identity",
    "customer_id",
    "validator_id",
    "assignment_id",
    "execution_client",
    "consensus_client",
)

# Object labels kube-state-metrics may expose, keyed by plural resource name.
# Intentionally minimal: only what the two joins need.
EXPECTED_ALLOWLIST = {
    "pods": {
        "platform.galaxy-lab/network",
        "platform.galaxy-lab/network-profile",
        "platform.galaxy-lab/network-generation",
        "platform.galaxy-lab/customer-id",
        "platform.galaxy-lab/validator-id",
        "platform.galaxy-lab/assignment-id",
        "platform.galaxy-lab/execution-client",
        "platform.galaxy-lab/consensus-client",
        "platform.galaxy-lab/component",
    },
    "persistentvolumeclaims": {
        "platform.galaxy-lab/network",
        "platform.galaxy-lab/network-profile",
        "platform.galaxy-lab/network-generation",
        "platform.galaxy-lab/customer-id",
        "platform.galaxy-lab/validator-id",
        "platform.galaxy-lab/assignment-id",
    },
}

# kube-state-metrics label series and the resource key each must be joined on.
JOIN_KEYS = {
    "kube_pod_labels": "on (namespace, pod)",
    "kube_persistentvolumeclaim_labels": "on (namespace, persistentvolumeclaim)",
}

PUBLIC_KEY_PATTERN = re.compile(r"0x[0-9a-fA-F]{96}")


def load_dashboards() -> list[tuple[str, dict]]:
    dashboards = []
    for path in sorted(LOCAL_APPS.glob("*dashboard.yaml")):
        with path.open(encoding="utf-8") as stream:
            config_map = yaml.safe_load(stream)
        for payload in config_map["data"].values():
            dashboards.append((path.name, json.loads(payload)))
    return dashboards


class ScrapeRelabelContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
        self.rules = (CHART / "templates" / "prometheusrule.yaml").read_text(encoding="utf-8")

    def test_scrape_relabelings_set_every_telemetry_label(self) -> None:
        relabelings = self.helpers.split('define "ethereum-node.metricRelabelings"', 1)[1]
        for label in TELEMETRY_LABELS:
            with self.subTest(label=label):
                self.assertIn(
                    f"targetLabel: {label}",
                    relabelings,
                    f"{label} must be attached at scrape time, not derived later",
                )

    def test_cluster_and_environment_are_required_values(self) -> None:
        """A missing environment identity must fail the render, not ship unlabelled."""
        relabelings = self.helpers.split('define "ethereum-node.metricRelabelings"', 1)[1]
        for value in ("telemetry.cluster", "telemetry.environment"):
            with self.subTest(value=value):
                self.assertIn(f'required "', relabelings)
                self.assertIn(f".Values.{value}", relabelings)

    def test_external_labels_are_not_used_as_a_query_label_substitute(self) -> None:
        """externalLabels apply on remote-write and federation, not to local queries."""
        monitoring = (CONTROLLERS / "monitoring.yaml").read_text(encoding="utf-8")
        self.assertNotIn("externalLabels", monitoring)

    def test_every_recording_rule_retains_the_full_label_set(self) -> None:
        aggregations = re.findall(r"\b(?:max|sum|min|count|avg) by \(([^)]*)\)", self.rules)
        self.assertTrue(aggregations, "expected aggregating recording rules")
        for clause in aggregations:
            with self.subTest(clause=clause.strip()[:60]):
                self.assertIn(
                    'include "ethereum-node.telemetryLabels"',
                    clause,
                    "aggregations must retain the shared telemetry label set",
                )

    def test_telemetry_label_helper_lists_every_label(self) -> None:
        body = self.helpers.split('define "ethereum-node.telemetryLabels" -}}', 1)[1]
        body = body.split("{{-", 1)[0]
        listed = {name.strip() for name in body.split(",") if name.strip()}
        self.assertEqual(listed, set(TELEMETRY_LABELS))

    def test_values_schema_requires_telemetry(self) -> None:
        schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
        self.assertIn("telemetry", schema["required"])
        telemetry = schema["properties"]["telemetry"]
        self.assertEqual(set(telemetry["required"]), {"cluster", "environment"})
        self.assertFalse(telemetry["additionalProperties"])

    def test_local_defaults_are_set_explicitly(self) -> None:
        values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(values["telemetry"]["cluster"], "kind-eth-validator-local")
        self.assertEqual(values["telemetry"]["environment"], "local")

    def test_rendered_generation_relabel_value_is_a_string(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "telemetry-type",
                str(CHART),
                "--namespace",
                "ethereum",
                "--set",
                "lifecycleState=active",
                "--set-string",
                "networkProfile.generation=162",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        pod_monitors = [
            document
            for document in yaml.safe_load_all(result.stdout)
            if document and document.get("kind") == "PodMonitor"
        ]
        self.assertTrue(pod_monitors)
        replacements = [
            relabel["replacement"]
            for monitor in pod_monitors
            for endpoint in monitor["spec"]["podMetricsEndpoints"]
            for relabel in endpoint["relabelings"]
            if relabel.get("targetLabel") == "network_generation"
        ]
        self.assertTrue(replacements)
        self.assertTrue(all(isinstance(value, str) for value in replacements))
        self.assertEqual(set(replacements), {"162"})


class KubeStateMetricsAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        with (CONTROLLERS / "monitoring.yaml").open(encoding="utf-8") as stream:
            self.release = yaml.safe_load(stream)
        self.allowlist = self.release["spec"]["values"]["kube-state-metrics"]["metricLabelsAllowlist"]

    def test_allowlist_uses_the_upstream_value_shape(self) -> None:
        """kube-state-metrics 7.4.0 joins this list into --metric-labels-allowlist."""
        self.assertIsInstance(self.allowlist, list)
        for entry in self.allowlist:
            with self.subTest(entry=entry[:40]):
                self.assertRegex(entry, r"^[a-z]+=\[[^\]]+\]$", "entries are resource=[label,...]")

    def test_allowlist_covers_both_join_resources(self) -> None:
        """Restarts key on the Pod and volume stats on the PVC — both are required."""
        resources = {entry.split("=", 1)[0] for entry in self.allowlist}
        self.assertEqual(resources, set(EXPECTED_ALLOWLIST))

    def test_allowlist_exposes_only_controlled_labels(self) -> None:
        for entry in self.allowlist:
            resource, raw = entry.split("=", 1)
            labels = set(raw.strip("[]").split(","))
            with self.subTest(resource=resource):
                self.assertEqual(labels, EXPECTED_ALLOWLIST[resource])
                self.assertNotIn("*", labels, "a wildcard allowlist has severe cardinality cost")

    def test_allowlist_never_exposes_key_material(self) -> None:
        joined = " ".join(self.allowlist).lower()
        for forbidden in ("public-key", "public_key", "pubkey", "secret", "password", "mnemonic"):
            with self.subTest(label=forbidden):
                self.assertNotIn(forbidden, joined)


class DashboardJoinContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboards = load_dashboards()

    def _expressions(self):
        for name, dashboard in self.dashboards:
            for panel in dashboard["panels"]:
                for target in panel.get("targets", []):
                    if "expr" in target:
                        yield dashboard["uid"], panel["title"], target["expr"]

    def test_label_joins_use_the_real_resource_key(self) -> None:
        seen = set()
        for uid, title, expression in self._expressions():
            for series, key in JOIN_KEYS.items():
                if series not in expression:
                    continue
                seen.add(series)
                with self.subTest(dashboard=uid, panel=title, series=series):
                    self.assertIn(
                        key,
                        expression,
                        f"{series} must be joined {key}, not on another resource's key",
                    )
                    self.assertIn("group_left()", expression)
        self.assertEqual(
            seen,
            set(JOIN_KEYS),
            "both label joins must be exercised by a dashboard panel",
        )

    def test_joined_panels_select_the_assignment_by_label(self) -> None:
        for uid, title, expression in self._expressions():
            if not any(series in expression for series in JOIN_KEYS):
                continue
            with self.subTest(dashboard=uid, panel=title):
                self.assertIn(
                    'label_platform_galaxy_lab_assignment_id=~"$assignment_id"',
                    expression,
                    "joined panels must select the assignment through the exposed label",
                )

    def test_no_dashboard_query_references_key_material(self) -> None:
        for uid, title, expression in self._expressions():
            with self.subTest(dashboard=uid, panel=title):
                self.assertIsNone(PUBLIC_KEY_PATTERN.search(expression))
                self.assertNotIn("public_key", expression.lower())
                self.assertNotIn("pubkey", expression.lower())


if __name__ == "__main__":
    unittest.main()
