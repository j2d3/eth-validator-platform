from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTROLLERS = REPOSITORY_ROOT / "platform" / "infrastructure" / "controllers"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise AssertionError(f"{path} did not contain one YAML object")
    return document


class ObservabilityContractTests(unittest.TestCase):
    def test_ethereum_pvc_capacity_rules_are_assignment_scoped_and_conservative(self) -> None:
        monitoring = load_yaml(CONTROLLERS / "monitoring.yaml")
        values = monitoring["spec"]["values"]
        groups = values["additionalPrometheusRulesMap"]["ethereum-pvc-capacity"][
            "groups"
        ]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "ethereum-pvc-capacity")
        self.assertEqual(groups[0]["interval"], "30s")

        rules = groups[0]["rules"]
        records = {rule["record"]: rule for rule in rules if "record" in rule}
        alerts = {rule["alert"]: rule for rule in rules if "alert" in rule}

        self.assertEqual(
            set(records),
            {
                "validator_platform_pvc_identity",
                "validator_platform_pvc_used_bytes",
                "validator_platform_pvc_capacity_bytes",
                "validator_platform_pvc_utilization_ratio",
                "validator_platform_pvc_growth_bytes_per_second",
                "validator_platform_pvc_projected_seconds_to_full",
            },
        )

        identity = records["validator_platform_pvc_identity"]
        self.assertIn("kube_persistentvolumeclaim_labels", identity["expr"])
        self.assertIn(
            'label_platform_galaxy_lab_assignment_id!=""', identity["expr"]
        )
        self.assertNotIn("persistentvolumeclaim=~", identity["expr"])
        self.assertEqual(identity["labels"]["cluster"], "kind-eth-validator-local")
        self.assertEqual(identity["labels"]["environment"], "local")
        for label in (
            "network",
            "network_profile",
            "network_generation",
            "customer_id",
            "validator_id",
            "assignment_id",
            "execution_client",
            "consensus_client",
            "lifecycle_state",
        ):
            self.assertIn(label, identity["expr"])

        for record in (
            "validator_platform_pvc_used_bytes",
            "validator_platform_pvc_capacity_bytes",
        ):
            expression = records[record]["expr"]
            self.assertIn("on (namespace, persistentvolumeclaim)", expression)
            self.assertIn("group_left(", expression)
            self.assertIn("validator_platform_pvc_identity", expression)

        growth = records["validator_platform_pvc_growth_bytes_per_second"]["expr"]
        self.assertIn("deriv(validator_platform_pvc_used_bytes[6h])", growth)
        self.assertIn("clamp_min(", growth)
        self.assertIn("offset 5h", growth)
        self.assertIn("present_over_time(", growth)

        projection = records["validator_platform_pvc_projected_seconds_to_full"][
            "expr"
        ]
        self.assertIn("clamp_min(", projection)
        self.assertIn("validator_platform_pvc_growth_bytes_per_second > 1024", projection)

        self.assertEqual(
            set(alerts),
            {
                "EthereumPersistentVolumeUtilizationHigh",
                "EthereumPersistentVolumeProjectedFull",
            },
        )
        utilization = alerts["EthereumPersistentVolumeUtilizationHigh"]
        self.assertEqual(utilization["expr"], "validator_platform_pvc_utilization_ratio > 0.85")
        self.assertEqual(utilization["for"], "30m")
        projection_alert = alerts["EthereumPersistentVolumeProjectedFull"]
        self.assertEqual(
            projection_alert["expr"],
            "validator_platform_pvc_projected_seconds_to_full < 604800",
        )
        self.assertEqual(projection_alert["for"], "30m")
        for alert in alerts.values():
            self.assertEqual(alert["labels"]["severity"], "warning")
            self.assertEqual(alert["labels"]["category"], "storage")
            self.assertIn("#persistent-volume-capacity", alert["annotations"]["runbook_url"])

        allowlist = values["kube-state-metrics"]["metricLabelsAllowlist"]
        pvc_allowlist = next(
            entry for entry in allowlist if entry.startswith("persistentvolumeclaims=")
        )
        for label in (
            "platform.galaxy-lab/assignment-id",
            "platform.galaxy-lab/execution-client",
            "platform.galaxy-lab/consensus-client",
            "platform.galaxy-lab/lifecycle",
        ):
            self.assertIn(label, pvc_allowlist)

    def test_dev_render_overrides_pvc_rule_environment_identity(self) -> None:
        result = subprocess.run(
            [
                "kubectl",
                "kustomize",
                str(
                    REPOSITORY_ROOT
                    / "platform"
                    / "infrastructure"
                    / "overlays"
                    / "dev"
                    / "controllers"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        release = next(
            document
            for document in yaml.safe_load_all(result.stdout)
            if document
            and document.get("kind") == "HelmRelease"
            and document["metadata"]["name"] == "kube-prometheus-stack"
        )
        groups = release["spec"]["values"]["additionalPrometheusRulesMap"][
            "ethereum-pvc-capacity"
        ]["groups"]
        identity = next(
            rule
            for group in groups
            for rule in group["rules"]
            if rule.get("record") == "validator_platform_pvc_identity"
        )
        self.assertEqual(identity["labels"]["cluster"], "eth-validator-platform-dev")
        self.assertEqual(identity["labels"]["environment"], "dev")

    def test_loki_is_bounded_persistent_single_binary(self) -> None:
        release = load_yaml(CONTROLLERS / "logging-loki.yaml")
        chart = release["spec"]["chart"]["spec"]
        values = release["spec"]["values"]

        self.assertEqual(chart["chart"], "loki")
        self.assertEqual(chart["version"], "7.2.0")
        self.assertEqual(values["deploymentMode"], "SingleBinary")
        self.assertEqual(values["singleBinary"]["replicas"], 1)
        self.assertEqual(values["singleBinary"]["persistence"]["size"], "5Gi")
        self.assertEqual(values["loki"]["limits_config"]["retention_period"], "24h")
        self.assertTrue(values["loki"]["compactor"]["retention_enabled"])
        self.assertFalse(values["loki"]["auth_enabled"])
        self.assertFalse(values["serviceAccount"]["automountServiceAccountToken"])
        self.assertTrue(values["rbac"]["namespaced"])
        self.assertFalse(values["sidecar"]["rules"]["enabled"])
        self.assertRegex(values["loki"]["image"]["digest"], r"^sha256:[0-9a-f]{64}$")

        for component in ("backend", "read", "write"):
            self.assertEqual(values[component]["replicas"], 0)

    def test_alloy_uses_node_scoped_api_collection_without_host_mounts(self) -> None:
        release = load_yaml(CONTROLLERS / "logging-alloy.yaml")
        chart = release["spec"]["chart"]["spec"]
        values = release["spec"]["values"]
        alloy = values["alloy"]
        config = alloy["configMap"]["content"]

        self.assertEqual(chart["chart"], "alloy")
        self.assertEqual(chart["version"], "1.11.0")
        self.assertEqual(values["controller"]["type"], "daemonset")
        self.assertFalse(alloy["mounts"]["varlog"])
        self.assertFalse(alloy["mounts"]["dockercontainers"])
        self.assertIn('field = "spec.nodeName="', config)
        self.assertIn('loki.source.kubernetes "pod_logs"', config)
        self.assertNotIn("loki.source.file", config)
        self.assertRegex(values["image"]["digest"], r"^sha256:[0-9a-f]{64}$")

        for label in (
            "cluster",
            "environment",
            "network",
            "network_profile",
            "network_generation",
            "network_identity",
            "customer_id",
            "validator_id",
            "assignment_id",
            "execution_client",
            "consensus_client",
            "component",
            "lifecycle_state",
        ):
            self.assertIn(label, config)

        granted_resources = {
            resource
            for rule in values["rbac"]["rules"] + values["rbac"]["clusterRules"]
            for resource in rule.get("resources", [])
        }
        self.assertEqual(granted_resources, {"namespaces", "nodes", "pods", "pods/log"})

    def test_grafana_provisions_stable_loki_datasource(self) -> None:
        monitoring = load_yaml(CONTROLLERS / "monitoring.yaml")
        datasources = monitoring["spec"]["values"]["grafana"]["additionalDataSources"]
        loki = next(source for source in datasources if source["uid"] == "loki")

        self.assertEqual(loki["type"], "loki")
        self.assertEqual(loki["url"], "http://loki.observability.svc.cluster.local:3100")
        self.assertEqual(loki["access"], "proxy")

    def test_ondelete_pairs_do_not_fire_the_stock_rollout_alert(self) -> None:
        monitoring = load_yaml(CONTROLLERS / "monitoring.yaml")
        values = monitoring["spec"]["values"]

        self.assertTrue(
            values["defaultRules"]["disabled"]["KubeStatefulSetUpdateNotRolledOut"]
        )
        groups = values["additionalPrometheusRulesMap"]["statefulset-rollout"]["groups"]
        rules = [rule for group in groups for rule in group["rules"]]
        rule = next(
            rule
            for rule in rules
            if rule["alert"] == "KubeStatefulSetUpdateNotRolledOut"
        )

        self.assertEqual(rule["for"], "15m")
        self.assertEqual(rule["labels"]["severity"], "warning")
        self.assertGreaterEqual(rule["expr"].count('namespace!="ethereum"'), 5)
        self.assertNotIn('namespace=~".*"', rule["expr"])

    def test_dashboard_payloads_are_valid_json_with_stable_uids(self) -> None:
        dashboard_paths = sorted(
            (REPOSITORY_ROOT / "platform" / "apps" / "local").glob("*dashboard.yaml")
        )
        self.assertGreaterEqual(len(dashboard_paths), 3)
        seen_uids: set[str] = set()

        for path in dashboard_paths:
            config_map = load_yaml(path)
            self.assertEqual(config_map["metadata"]["labels"]["grafana_dashboard"], "1")
            for name, payload in config_map["data"].items():
                with self.subTest(path=path, dashboard=name):
                    dashboard = json.loads(payload)
                    uid = dashboard["uid"]
                    self.assertNotIn(uid, seen_uids)
                    seen_uids.add(uid)
                    self.assertTrue(dashboard["title"])
                    self.assertTrue(dashboard["panels"])

    def test_logging_policies_select_only_logging_workloads(self) -> None:
        policy_path = CONTROLLERS / "logging-networkpolicies.yaml"
        with policy_path.open(encoding="utf-8") as stream:
            policies = tuple(yaml.safe_load_all(stream))

        self.assertEqual({policy["metadata"]["name"] for policy in policies}, {"alloy", "loki"})
        for policy in policies:
            labels = policy["spec"]["podSelector"]["matchLabels"]
            self.assertEqual(labels["app.kubernetes.io/instance"], policy["metadata"]["name"])
            self.assertEqual(labels["app.kubernetes.io/name"], policy["metadata"]["name"])

        by_name = {policy["metadata"]["name"]: policy for policy in policies}
        loki_ingress_ports = {
            port["port"]
            for rule in by_name["loki"]["spec"]["ingress"]
            for port in rule["ports"]
        }
        self.assertTrue({3100, 3500, 7946, 9095}.issubset(loki_ingress_ports))

        alloy_egress_ports = {
            port["port"]
            for rule in by_name["alloy"]["spec"]["egress"]
            for port in rule["ports"]
        }
        self.assertTrue({53, 443, 3100, 6443}.issubset(alloy_egress_ports))


if __name__ == "__main__":
    unittest.main()
