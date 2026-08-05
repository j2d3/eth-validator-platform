"""Contracts every provisioned Grafana dashboard must satisfy.

These are structural guarantees that hold offline: payloads parse, identifiers
are stable and unique, the navigation dashboards share one variable contract,
and no dashboard puts a full validator public key on a query or label. Runtime
panel population is separate evidence and is not asserted here.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCAL_APPS = REPOSITORY_ROOT / "platform" / "apps" / "local"
EKS_NODE_APPS = REPOSITORY_ROOT / "platform" / "apps" / "nodes" / "dev"
PORTAL_REGISTRY = (
    REPOSITORY_ROOT / "control-plane" / "portal" / "lib" / "portal-registry.ts"
)

# The two metric dashboards that carry the fleet -> validator navigation contract.
NAVIGATION_UIDS = ("eth-fleet-overview", "eth-validator-detail")

# The log dashboard shares the same identity contract, backed by Alloy stream
# labels, so context survives a jump from either metric dashboard.
LOG_UID = "eth-platform-logs"

# Dashboard variable -> Loki stream label, where the names differ.
LOG_LABEL_FOR_VARIABLE = {
    "network": "network",
    "customer_id": "customer_id",
    "validator_id": "validator_id",
    "assignment_id": "assignment_id",
    "execution_client": "execution_client",
    "consensus_client": "consensus_client",
    "lifecycle": "lifecycle_state",
    "cluster": "cluster",
}

# Kubernetes resource metrics carry the resource name, not platform identity.
# The chart renders StatefulSet/ethereum-node-ethereum-node and claims
# ethereum-node-ethereum-node-{execution,consensus}; the assignment appears only
# as the object label platform.galaxy-lab/assignment-id. Correlating by matching
# a dashboard variable as a name substring therefore silently returns nothing.
NAME_SUBSTRING_CORRELATION = re.compile(
    r"(pod|persistentvolumeclaim|container|job|instance)\s*=~\s*\"[^\"]*\.\*\$"
)

# Every dashboard reachable from the navigation contract, including the
# pre-existing pair and log dashboards that it links to.
REQUIRED_VARIABLES = (
    "cluster",
    "network",
    "customer_id",
    "validator_id",
    "assignment_id",
    "execution_client",
    "consensus_client",
    "lifecycle",
)

# Dashboards each navigation dashboard must link to, so a reader can always
# move between fleet, validator, pair and log views without losing context.
REQUIRED_LINK_TARGETS = {
    "eth-fleet-overview": {"eth-validator-detail", "eth-validator-geth-lighthouse", "eth-platform-logs"},
    "eth-validator-detail": {"eth-fleet-overview", "eth-validator-geth-lighthouse", "eth-platform-logs"},
}

# A BLS public key as constrained by schemas/validator-identity.schema.json.
PUBLIC_KEY_PATTERN = re.compile(r"0x[0-9a-fA-F]{96}")

# Label names that would carry a public key onto a metric series.
FORBIDDEN_LABEL_NAMES = ("pubkey", "public_key", "publickey", "validator_pubkey")


def user_visible_text(dashboard: dict) -> str:
    """Return dashboard copy visible in Grafana, excluding stable URLs and UIDs."""
    values = [dashboard.get("title", ""), dashboard.get("description", "")]
    values.extend(dashboard.get("tags", []))
    for link in dashboard.get("links", []):
        values.extend((link.get("title", ""), link.get("tooltip", "")))
    for panel in dashboard.get("panels", []):
        values.extend((panel.get("title", ""), panel.get("description", "")))
        values.append(panel.get("options", {}).get("content", ""))
        for target in panel.get("targets", []):
            values.append(target.get("legendFormat", ""))
    return " ".join(value for value in values if isinstance(value, str))


def load_dashboards() -> list[tuple[Path, str, dict]]:
    """Return (path, payload name, parsed dashboard) for every provisioned dashboard."""
    dashboards: list[tuple[Path, str, dict]] = []
    for directory in (LOCAL_APPS, EKS_NODE_APPS):
        for path in sorted(directory.glob("*dashboard.yaml")):
            with path.open(encoding="utf-8") as stream:
                config_map = yaml.safe_load(stream)
            for name, payload in config_map["data"].items():
                dashboards.append((path, name, json.loads(payload)))
    return dashboards


class DashboardContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboards = load_dashboards()
        self.by_uid = {dashboard["uid"]: dashboard for _, _, dashboard in self.dashboards}

    def test_every_payload_is_valid_json_and_provisioned(self) -> None:
        self.assertGreaterEqual(len(self.dashboards), 5)
        for path, name, dashboard in self.dashboards:
            with self.subTest(path=path.name, dashboard=name):
                # load_dashboards() already parsed the JSON; assert the shape.
                self.assertTrue(dashboard["uid"], "dashboard requires a stable uid")
                self.assertTrue(dashboard["title"], "dashboard requires a title")
                self.assertTrue(dashboard["panels"], "dashboard requires panels")
                self.assertFalse(dashboard.get("editable", False), "dashboards are GitOps-provisioned")

        for path in sorted(LOCAL_APPS.glob("*dashboard.yaml")):
            with path.open(encoding="utf-8") as stream:
                config_map = yaml.safe_load(stream)
            with self.subTest(path=path.name):
                self.assertEqual(config_map["kind"], "ConfigMap")
                self.assertEqual(config_map["metadata"]["labels"]["grafana_dashboard"], "1")
                self.assertEqual(config_map["metadata"]["namespace"], "observability")

    def test_dashboard_uids_are_unique(self) -> None:
        uids = [dashboard["uid"] for _, _, dashboard in self.dashboards]
        duplicates = {uid for uid in uids if uids.count(uid) > 1}
        self.assertEqual(duplicates, set(), f"duplicate dashboard uids: {sorted(duplicates)}")

    def test_portal_direct_links_use_only_git_provisioned_dashboard_uids(self) -> None:
        registry = PORTAL_REGISTRY.read_text(encoding="utf-8")
        linked_uids = set(re.findall(r"\$\{grafanaBase\}/d/([^/`]+)", registry))
        self.assertTrue(linked_uids, "portal should link its provisioned pair dashboard")
        self.assertEqual(
            linked_uids - set(self.by_uid),
            set(),
            "portal direct links may use only dashboard UIDs provisioned by this repository",
        )

    def test_panel_ids_are_unique_and_stable_within_each_dashboard(self) -> None:
        for path, name, dashboard in self.dashboards:
            with self.subTest(path=path.name, dashboard=name):
                ids = [panel["id"] for panel in dashboard["panels"]]
                for panel_id in ids:
                    self.assertIsInstance(panel_id, int)
                    self.assertGreater(panel_id, 0)
                self.assertEqual(len(ids), len(set(ids)), f"duplicate panel ids in {dashboard['uid']}: {ids}")

    def test_navigation_dashboards_are_provisioned(self) -> None:
        for uid in NAVIGATION_UIDS:
            self.assertIn(uid, self.by_uid, f"navigation dashboard {uid} is not provisioned")

    def test_navigation_dashboards_share_the_variable_contract(self) -> None:
        for uid in NAVIGATION_UIDS:
            dashboard = self.by_uid[uid]
            with self.subTest(dashboard=uid):
                names = [variable["name"] for variable in dashboard["templating"]["list"]]
                self.assertEqual(
                    names,
                    list(REQUIRED_VARIABLES),
                    "navigation dashboards must declare the same variables in the same order",
                )

    def test_every_navigation_variable_is_a_real_query(self) -> None:
        """No placeholder variables.

        `cluster` and `lifecycle` used to be a constant and a log-only custom
        list because neither existed as a Prometheus label. The pair chart now
        sets `cluster`, `environment` and `lifecycle_state` in its scrape
        relabel contract, so every variable on a metric dashboard must be a
        genuine query against a real label.
        """
        for uid in NAVIGATION_UIDS:
            for variable in self.by_uid[uid]["templating"]["list"]:
                with self.subTest(dashboard=uid, variable=variable["name"]):
                    self.assertEqual(
                        variable["type"],
                        "query",
                        f"{variable['name']} must be backed by a real label query",
                    )
                    self.assertEqual(variable["datasource"]["uid"], "prometheus")

    def test_navigation_panels_filter_on_cluster_and_lifecycle(self) -> None:
        """Declaring the labels is not enough; the panels must select on them."""
        for uid in NAVIGATION_UIDS:
            for panel in self.by_uid[uid]["panels"]:
                for target in panel.get("targets", []):
                    expression = target.get("expr", "")
                    if "validator_platform_" not in expression:
                        continue
                    with self.subTest(dashboard=uid, panel=panel["title"]):
                        self.assertIn('cluster=~"$cluster"', expression)
                        self.assertIn('lifecycle_state=~"$lifecycle"', expression)

    def test_validator_detail_uses_normalized_pvc_capacity_series(self) -> None:
        dashboard = self.by_uid["eth-validator-detail"]
        panels = {panel["title"]: panel for panel in dashboard["panels"]}
        expected = {
            "Persistent volume usage": {
                "validator_platform_pvc_used_bytes",
                "validator_platform_pvc_capacity_bytes",
            },
            "Persistent volume utilization": {
                "validator_platform_pvc_utilization_ratio"
            },
            "Projected time to full": {
                "validator_platform_pvc_projected_seconds_to_full"
            },
        }
        for title, metrics in expected.items():
            with self.subTest(panel=title):
                self.assertIn(title, panels)
                expressions = "\n".join(
                    target["expr"] for target in panels[title].get("targets", [])
                )
                for metric in metrics:
                    self.assertIn(metric, expressions)
                self.assertNotIn("kubelet_volume_stats_", expressions)
                self.assertIn('assignment_id=~"$assignment_id"', expressions)
                self.assertIn('validator_id=~"$validator_id"', expressions)

        projection = panels["Projected time to full"]
        self.assertIn("five hours", projection["description"])
        self.assertIn("seven days", projection["description"])

    def test_navigation_links_reach_the_related_dashboards(self) -> None:
        for uid, expected_targets in REQUIRED_LINK_TARGETS.items():
            dashboard = self.by_uid[uid]
            with self.subTest(dashboard=uid):
                links = dashboard.get("links", [])
                linked = {link["url"].rsplit("/", 1)[-1] for link in links if link.get("url")}
                self.assertTrue(
                    expected_targets.issubset(linked),
                    f"{uid} is missing links to {sorted(expected_targets - linked)}",
                )
                for link in links:
                    self.assertTrue(link.get("includeVars"), "links must carry variables across dashboards")
                    self.assertTrue(link.get("keepTime"), "links must carry the time range across dashboards")

    def test_navigation_link_targets_exist(self) -> None:
        for uid, expected_targets in REQUIRED_LINK_TARGETS.items():
            for target in expected_targets:
                with self.subTest(dashboard=uid, target=target):
                    self.assertIn(target, self.by_uid, f"{uid} links to unknown dashboard {target}")

    def test_no_dashboard_exposes_a_full_public_key(self) -> None:
        for path, name, dashboard in self.dashboards:
            payload = json.dumps(dashboard)
            with self.subTest(path=path.name, dashboard=name):
                self.assertIsNone(
                    PUBLIC_KEY_PATTERN.search(payload),
                    f"{dashboard['uid']} contains a full BLS public key",
                )
                lowered = payload.lower()
                for label in FORBIDDEN_LABEL_NAMES:
                    self.assertNotIn(
                        label,
                        lowered,
                        f"{dashboard['uid']} references a public-key label {label!r}",
                    )

    def test_dashboard_copy_is_role_based_and_neutral(self) -> None:
        """User-facing labels describe roles and signals, not the first pair or a reaction."""
        for path, name, dashboard in self.dashboards:
            copy = user_visible_text(dashboard).lower()
            with self.subTest(path=path.name, dashboard=name):
                self.assertNotIn("geth", copy)
                self.assertNotIn("lighthouse", copy)
                self.assertNotIn("celebrat", copy)

    def test_navigation_panels_declare_an_explicit_no_value_state(self) -> None:
        """Missing telemetry must never render as a confident zero."""
        for uid in NAVIGATION_UIDS:
            dashboard = self.by_uid[uid]
            for panel in dashboard["panels"]:
                if panel["type"] == "text":
                    continue
                with self.subTest(dashboard=uid, panel=panel["title"]):
                    no_value = panel.get("fieldConfig", {}).get("defaults", {}).get("noValue")
                    self.assertTrue(
                        no_value,
                        f"panel {panel['title']!r} must declare a noValue state rather than implying zero",
                    )

    def test_navigation_dashboards_state_what_is_not_collected(self) -> None:
        for uid in NAVIGATION_UIDS:
            dashboard = self.by_uid[uid]
            text_panels = [p for p in dashboard["panels"] if p["type"] == "text"]
            with self.subTest(dashboard=uid):
                self.assertTrue(text_panels, "navigation dashboards must carry explanatory text panels")
                combined = " ".join(
                    f"{p.get('title', '')} {p['options']['content']}" for p in text_panels
                ).lower()
                self.assertIn("not collected", combined)
                # The absence must be attributed, not merely asserted.
                self.assertTrue(
                    any(
                        reason in combined
                        for reason in ("no verified normalization", "out of scope", "by contract")
                    ),
                    "each absent signal must carry a stated reason",
                )

    @staticmethod
    def _expressions(dashboard: dict) -> list[str]:
        return [
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
            if "expr" in target
        ]

    def test_log_dashboard_defines_and_consumes_the_identity_contract(self) -> None:
        """A link may only promise context the destination can actually filter on.

        Alloy emits every one of these as a Loki stream label, so the log
        dashboard must both declare the variable and use it in a selector.
        Declaring it without consuming it would make navigation look like it
        preserves context while silently ignoring it.
        """
        dashboard = self.by_uid[LOG_UID]
        declared = {variable["name"] for variable in dashboard["templating"]["list"]}
        expressions = " ".join(self._expressions(dashboard))

        for variable, label in LOG_LABEL_FOR_VARIABLE.items():
            with self.subTest(variable=variable):
                self.assertIn(variable, declared, f"log dashboard must declare ${variable}")
                self.assertIn(
                    f'{label}=~"${variable}"',
                    expressions,
                    f"log dashboard declares ${variable} but never filters on {label}",
                )

    def test_link_promised_variables_exist_on_the_destination(self) -> None:
        """Every var-* a data link sets must be a variable the destination defines."""
        for _, _, dashboard in self.dashboards:
            for panel in dashboard["panels"]:
                links = panel.get("fieldConfig", {}).get("defaults", {}).get("links", [])
                for link in links:
                    url = link.get("url", "")
                    target_uid = url.split("?", 1)[0].rsplit("/", 1)[-1]
                    promised = set(re.findall(r"var-([A-Za-z_][A-Za-z0-9_]*)=", url))
                    with self.subTest(source=dashboard["uid"], target=target_uid):
                        self.assertIn(target_uid, self.by_uid, f"link to unknown dashboard {target_uid}")
                        destination = {
                            variable["name"]
                            for variable in self.by_uid[target_uid]["templating"]["list"]
                        }
                        missing = promised - destination
                        self.assertEqual(
                            missing,
                            set(),
                            f"{dashboard['uid']} promises {sorted(missing)} that {target_uid} does not define",
                        )

    def test_link_carries_the_full_identity_contract(self) -> None:
        """The fleet row link must pass every identity variable, not a subset."""
        fleet = self.by_uid["eth-fleet-overview"]
        row_links = [
            link
            for panel in fleet["panels"]
            for link in panel.get("fieldConfig", {}).get("defaults", {}).get("links", [])
        ]
        self.assertTrue(row_links, "fleet inventory must expose a drill-down link")
        for link in row_links:
            promised = set(re.findall(r"var-([A-Za-z_][A-Za-z0-9_]*)=", link["url"]))
            self.assertEqual(
                promised,
                set(REQUIRED_VARIABLES),
                f"row link must carry every identity variable; missing {sorted(set(REQUIRED_VARIABLES) - promised)}",
            )

    def test_no_panel_correlates_kubernetes_resources_by_name_substring(self) -> None:
        """Identity lives in object labels, never in rendered resource names.

        `helm template ethereum-node charts/ethereum-node --set lifecycleState=active`
        renders `ethereum-node-ethereum-node` and `ethereum-node-ethereum-node-execution`
        while the assignment is only the label `platform.galaxy-lab/assignment-id`.
        A `pod=~".*$assignment_id.*"` style matcher therefore returns no data and
        is indistinguishable from missing telemetry.
        """
        for path, name, dashboard in self.dashboards:
            for expression in self._expressions(dashboard):
                with self.subTest(dashboard=dashboard["uid"], expr=expression[:60]):
                    self.assertIsNone(
                        NAME_SUBSTRING_CORRELATION.search(expression),
                        "correlate Kubernetes resources by an exposed label, not a name substring",
                    )

    def test_chart_does_not_encode_identity_in_resource_names(self) -> None:
        """Guards the premise of the test above: identity is a label, not a name."""
        helpers = (
            REPOSITORY_ROOT / "charts" / "ethereum-node" / "templates" / "_helpers.tpl"
        ).read_text(encoding="utf-8")
        fullname = helpers.split('define "ethereum-node.fullname"', 1)[1].split("end", 1)[0]
        for identity in ("assignmentId", "validatorId", "customerId"):
            self.assertNotIn(
                identity,
                fullname,
                f"{identity} is not part of the resource name, so names cannot be matched on it",
            )
        self.assertIn("platform.galaxy-lab/assignment-id", helpers)

    def test_navigation_panels_only_use_declared_metrics(self) -> None:
        """Guard against inventing metric names: every platform series must be a known recording rule."""
        rule_source = (
            REPOSITORY_ROOT / "charts" / "ethereum-node" / "templates" / "prometheusrule.yaml"
        ).read_text(encoding="utf-8")
        declared = set(re.findall(r"- record:\s*(\S+)", rule_source))
        monitoring_path = (
            REPOSITORY_ROOT
            / "platform"
            / "infrastructure"
            / "controllers"
            / "monitoring.yaml"
        )
        monitoring = yaml.safe_load(monitoring_path.read_text(encoding="utf-8"))
        shared_rule_maps = monitoring["spec"]["values"]["additionalPrometheusRulesMap"]
        declared.update(
            rule["record"]
            for rule_map in shared_rule_maps.values()
            for group in rule_map["groups"]
            for rule in group["rules"]
            if "record" in rule
        )
        self.assertTrue(
            declared,
            "expected recording rules in the pair chart or shared monitoring release",
        )

        for uid in NAVIGATION_UIDS:
            dashboard = self.by_uid[uid]
            for panel in dashboard["panels"]:
                for target in panel.get("targets", []):
                    expression = target.get("expr", "")
                    for referenced in re.findall(r"\bvalidator_platform_\w+", expression):
                        with self.subTest(dashboard=uid, panel=panel["title"], metric=referenced):
                            self.assertIn(
                                referenced,
                                declared,
                                f"{referenced} is not a declared recording rule",
                            )


if __name__ == "__main__":
    unittest.main()
