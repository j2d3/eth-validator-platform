"""Contracts for the curated portal telemetry adapter."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "platform" / "apps" / "portal" / "dev"
SERVER_PATH = APP / "server.py"
CLUSTER = ROOT / "clusters" / "dev"

SPEC = importlib.util.spec_from_file_location("portal_status_api", SERVER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load portal status API")
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


def vector(value: int | float, **labels: str) -> list[dict]:
    return [{"metric": labels, "value": [1_700_000_000, str(value)]}]


class FakePrometheus:
    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.queries: list[str] = []

    def query(self, expression: str) -> list[dict]:
        self.queries.append(expression)
        return self.results.get(expression, [])


def base_results() -> dict[str, list[dict]]:
    results = {
        expression: []
        for expression in {**SERVER.SCALAR_QUERIES, **SERVER.PAIR_QUERIES}.values()
    }
    scalar_values = {
        "sourceReady": 1,
        "nodesReady": 2,
        "clusterCpuAllocatableCores": 3.86,
        "clusterMemoryAllocatableBytes": 14_899_040_256,
        "clusterCpuUsageCores": 0.25,
        "clusterMemoryUsageBytes": 1_500_000_000,
        "clusterPods": 26,
        "clusterPodsRunning": 25,
        "clusterPodsPending": 1,
        "clusterContainerRestarts": 4,
        "nodeGroupLabels": 2,
        "systemNodesReady": 2,
        "ethereumNodesReady": 0,
        "ethereumPods": 2,
        "ethereumPodsRunning": 2,
        "ethereumCpuCores": 0.12,
        "ethereumMemoryBytes": 900_000_000,
        "ethereumRestarts": 1,
        "ethereumVolumeUsedBytes": 80_000_000_000,
        "ethereumVolumeCapacityBytes": 200_000_000_000,
        "signerUp": 1,
        "signerKeysLoaded": 1,
        "signingValidatorsEnabled": 1,
        "signingPermittedTotal": 2,
        "signingPreventedTotal": 0,
        "signingMissingIdentifierTotal": 0,
        "firingAlertsTotal": 2,
        "firingAlertsCritical": 1,
        "firingAlertsWarning": 1,
    }
    for name, value in scalar_values.items():
        results[SERVER.SCALAR_QUERIES[name]] = vector(value)
    return results


class PortalStatusApiResponseTests(unittest.TestCase):
    def test_snapshot_contains_observed_cluster_and_pair_metrics(self) -> None:
        results = base_results()
        pair_labels = {
            "assignment_id": "assignment-ephemery-162-synthetic",
            "cluster": "eth-validator-platform-dev",
            "environment": "dev",
            "network": "ephemery",
            "network_profile": "ephemery",
            "network_generation": "162",
            "execution_client": "geth",
            "consensus_client": "lighthouse",
            "lifecycle_state": "active",
            "customer_id": "must-not-leak",
            "validator_id": "must-not-leak",
            "network_identity": "must-not-leak",
        }
        results[SERVER.PAIR_QUERIES["targetUp"]] = [
            *vector(1, **pair_labels, component="execution"),
            *vector(1, **pair_labels, component="consensus"),
        ]
        results[SERVER.PAIR_QUERIES["executionPeers"]] = vector(14, **pair_labels)
        results[SERVER.PAIR_QUERIES["consensusPeers"]] = vector(31, **pair_labels)
        results[SERVER.PAIR_QUERIES["validatorEnabled"]] = vector(
            1, **pair_labels
        )
        results[SERVER.PAIR_QUERIES["containerCpuCores"]] = vector(
            0.08, **pair_labels, component="execution"
        )
        results[SERVER.PAIR_QUERIES["containerMemoryWorkingSetBytes"]] = vector(
            512_000_000, **pair_labels, component="execution"
        )

        with patch.object(
            SERVER, "GRAFANA_BASE_URL", "https://ops.g.j2d3.com/grafana"
        ):
            client = FakePrometheus(results)
            snapshot = SERVER.build_snapshot(client)

        self.assertEqual(snapshot["schemaVersion"], 1)
        self.assertEqual(snapshot["cluster"]["nodes"]["ready"], 2)
        self.assertEqual(snapshot["cluster"]["capacity"]["cpuCores"], 3.86)
        self.assertEqual(snapshot["cluster"]["pods"]["pending"], 1)
        self.assertEqual(
            snapshot["cluster"]["ethereumWorkloads"]["persistentVolumeBytes"],
            {"used": 80_000_000_000, "capacity": 200_000_000_000},
        )
        self.assertEqual(len(snapshot["pairs"]), 1)
        pair = snapshot["pairs"][0]
        self.assertEqual(pair["targets"], {"execution": 1, "consensus": 1})
        self.assertEqual(pair["sync"]["executionPeers"], 14)
        self.assertEqual(pair["sync"]["consensusPeers"], 31)
        self.assertEqual(pair["signing"]["validatorsEnabled"], 1)
        self.assertEqual(pair["resources"]["cpuCores"]["execution"], 0.08)
        self.assertEqual(
            pair["resources"]["memoryBytes"]["execution"], 512_000_000
        )
        self.assertEqual(
            snapshot["signing"],
            {
                "validatorsEnabled": 1,
                "signerUp": 1,
                "keysLoaded": 1,
                "slashingPermittedTotal": 2,
                "slashingPreventedTotal": 0,
                "missingIdentifierTotal": 0,
            },
        )
        self.assertEqual(
            snapshot["alerts"],
            {"firingTotal": 2, "critical": 1, "warning": 1},
        )
        self.assertIn('alertname!="Watchdog"', SERVER.SCALAR_QUERIES["firingAlertsTotal"])
        self.assertIn('severity="critical"', SERVER.SCALAR_QUERIES["firingAlertsCritical"])
        self.assertIn('severity="warning"', SERVER.SCALAR_QUERIES["firingAlertsWarning"])

        grafana = urlsplit(pair["grafanaUrl"])
        self.assertEqual((grafana.scheme, grafana.netloc), ("https", "ops.g.j2d3.com"))
        self.assertEqual(
            grafana.path,
            "/grafana/d/eth-eks-ephemery-sync/"
            "ethereum-platform-eks-ephemery-sync-evidence",
        )
        self.assertEqual(
            parse_qs(grafana.query)["var-assignment_id"],
            ["assignment-ephemery-162-synthetic"],
        )

        encoded = json.dumps(snapshot)
        for forbidden in (
            "customer_id",
            "validator_id",
            "network_identity",
            "must-not-leak",
            "public_key",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(
            set(client.queries),
            set(SERVER.SCALAR_QUERIES.values()) | set(SERVER.PAIR_QUERIES.values()),
        )

    def test_missing_node_group_labels_are_explicitly_unavailable(self) -> None:
        results = base_results()
        results[SERVER.SCALAR_QUERIES["nodeGroupLabels"]] = []
        snapshot = SERVER.build_snapshot(FakePrometheus(results))
        self.assertIsNone(snapshot["cluster"]["nodes"]["systemReady"])
        self.assertIsNone(snapshot["cluster"]["nodes"]["ethereumReady"])

    def test_pair_without_validator_metric_reports_explicit_null(self) -> None:
        results = base_results()
        pair_labels = {
            "assignment_id": "assignment-ephemery-162-synthetic-reth",
            "cluster": "eth-validator-platform-dev",
            "environment": "dev",
            "network": "ephemery",
            "network_profile": "ephemery",
            "network_generation": "162",
            "execution_client": "reth",
            "consensus_client": "lighthouse",
            "lifecycle_state": "active",
        }
        results[SERVER.PAIR_QUERIES["targetUp"]] = [
            *vector(1, **pair_labels, component="execution"),
            *vector(1, **pair_labels, component="consensus"),
        ]

        snapshot = SERVER.build_snapshot(FakePrometheus(results))

        self.assertEqual(
            snapshot["pairs"][0]["signing"], {"validatorsEnabled": None}
        )

    def test_no_target_series_means_no_pair_and_no_dead_grafana_link(self) -> None:
        snapshot = SERVER.build_snapshot(FakePrometheus(base_results()))
        self.assertEqual(snapshot["pairs"], [])
        labels = {"assignment_id": "assignment-a"}
        with patch.object(SERVER, "GRAFANA_BASE_URL", ""):
            self.assertIsNone(SERVER._grafana_url(labels))

    def test_configuration_rejects_non_https_or_wrong_grafana_path(self) -> None:
        with patch.object(SERVER, "GRAFANA_BASE_URL", "http://example.org/grafana"):
            with self.assertRaisesRegex(ValueError, "must use HTTPS"):
                SERVER._validate_configuration()
        with patch.object(SERVER, "GRAFANA_BASE_URL", "https://example.org/other"):
            with self.assertRaisesRegex(ValueError, "must end at /grafana"):
                SERVER._validate_configuration()


class PortalStatusApiManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rendered = subprocess.run(
            ["kubectl", "kustomize", str(APP)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        cls.objects = [item for item in yaml.safe_load_all(rendered) if item]

    def object(self, kind: str, name: str) -> dict:
        return next(
            item
            for item in self.objects
            if item["kind"] == kind and item["metadata"]["name"] == name
        )

    def test_workload_is_digest_pinned_restricted_and_secret_free(self) -> None:
        deployment = self.object("Deployment", "portal-status-api")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(pod["automountServiceAccountToken"], False)
        self.assertEqual(pod["securityContext"]["runAsUser"], 65532)
        self.assertEqual(pod["securityContext"]["runAsGroup"], 65532)
        self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        self.assertRegex(container["image"], r"@sha256:[0-9a-f]{64}$")
        self.assertEqual(
            container["command"], ["/usr/local/bin/python", "/app/server.py"]
        )
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertIn("resources", container)
        self.assertIn("readinessProbe", container)
        self.assertIn("livenessProbe", container)
        env_names = {item["name"] for item in container["env"]}
        self.assertNotIn("valueFrom", yaml.safe_dump(container["env"]))
        grafana_url = next(
            item["value"]
            for item in container["env"]
            if item["name"] == "GRAFANA_BASE_URL"
        )
        self.assertEqual(grafana_url, "https://ops.g.j2d3.com/grafana")
        prometheus_url = next(
            item["value"]
            for item in container["env"]
            if item["name"] == "PROMETHEUS_URL"
        )
        self.assertEqual(
            prometheus_url,
            "http://prometheus-operated.observability.svc.cluster.local:9090",
        )

    def test_service_is_cluster_private_and_ingress_exposes_one_exact_path(self) -> None:
        service = self.object("Service", "portal-status-api")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        ingress = self.object("Ingress", "portal-status-api")
        self.assertEqual(ingress["spec"]["ingressClassName"], "nginx")
        self.assertEqual(len(ingress["spec"]["rules"]), 1)
        rule = ingress["spec"]["rules"][0]
        self.assertEqual(rule["host"], "ops.g.j2d3.com")
        self.assertEqual(len(rule["http"]["paths"]), 1)
        self.assertEqual(rule["http"]["paths"][0]["path"], "/api/status")
        self.assertEqual(rule["http"]["paths"][0]["pathType"], "Exact")

    def test_network_policy_allows_only_ingress_controller_dns_and_prometheus(self) -> None:
        policy = self.object("NetworkPolicy", "portal-status-api")["spec"]
        self.assertEqual(policy["policyTypes"], ["Ingress", "Egress"])
        self.assertEqual(
            policy["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"],
            {"kubernetes.io/metadata.name": "ingress-nginx"},
        )
        self.assertEqual(len(policy["egress"]), 2)
        dumped = yaml.safe_dump(policy)
        self.assertIn("monitoring-prometheus", dumped)
        self.assertNotIn("ipBlock", dumped)

    def test_flux_layer_is_independent_unsuspended_and_waiting(self) -> None:
        layer = yaml.safe_load((CLUSTER / "portal-observability.yaml").read_text())
        self.assertEqual(layer["spec"]["path"], "./platform/apps/portal/dev")
        self.assertEqual(layer["spec"]["dependsOn"], [{"name": "infrastructure-configs"}])
        self.assertNotIn("suspend", layer["spec"])
        self.assertTrue(layer["spec"]["wait"])
        root = yaml.safe_load((CLUSTER / "kustomization.yaml").read_text())
        self.assertIn("portal-observability.yaml", root["resources"])


if __name__ == "__main__":
    unittest.main()
