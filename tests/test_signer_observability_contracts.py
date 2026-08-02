"""Signer and slashing-protection observability contracts.

The governing rule for this slice is that no signal may be invented. Every
Web3Signer metric name below was read from the pinned
consensys/web3signer:26.4.2 image's own /metrics endpoint, and every database
metric from the pinned cloudnative-pg 0.29.0 cnpg-default-monitoring query set.
These tests pin the dashboard and recording rules to exactly those names, so a
plausible-sounding but non-existent metric fails CI rather than rendering as an
empty panel someone mistakes for an outage.

Runtime behaviour is separate evidence and is not asserted here.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SIGNER_BASE = REPOSITORY_ROOT / "platform" / "apps" / "base" / "web3signer"
LOCAL_APPS = REPOSITORY_ROOT / "platform" / "apps" / "local"
SIGNER_DASHBOARD_UID = "eth-signer-slashing"

# Read from the pinned image's /metrics endpoint. Confirmed identical with
# --slashing-protection-enabled both false and true against a migrated
# PostgreSQL: 48 families either way, so enabling the database adds nothing.
VERIFIED_WEB3SIGNER_METRICS = {
    "eth2_slashingprotection_permitted_signings_total",
    "eth2_slashingprotection_prevented_signings_total",
    "signing_signers_loaded_count",
    "signing_bls_missing_identifier_count_total",
    "signing_bls_signing_duration",
    "http_bls_malformed_request_count_total",
}

# From the pinned cloudnative-pg 0.29.0 cnpg-default-monitoring queries.
VERIFIED_CNPG_METRICS = {
    "cnpg_backends_total",
    "cnpg_backends_max_tx_duration_seconds",
    "cnpg_pg_database_size_bytes",
    "cnpg_pg_database_xid_age",
    "cnpg_pg_postmaster_start_time",
    "cnpg_pg_stat_database_xact_commit",
    "cnpg_pg_stat_database_xact_rollback",
    "cnpg_pg_stat_database_deadlocks",
    "cnpg_pg_stat_database_conflicts",
    "cnpg_pg_stat_database_blks_read",
    "cnpg_pg_stat_database_blks_hit",
}

# Normalized names this slice introduces.
SIGNER_RECORDING_RULES = {
    "validator_platform_signer_up",
    "validator_platform_signer_keys_loaded",
    "validator_platform_signer_slashing_permitted_total",
    "validator_platform_signer_slashing_prevented_total",
    "validator_platform_signer_missing_identifier_total",
}

# Web3Signer exposes no database or connection-pool metric at all; the probe
# found only JVM and Vert.x pools. Anything matching these would be invented.
FORBIDDEN_SIGNER_DB_PATTERNS = (
    "hikari",
    "web3signer_db",
    "signer_db_connection",
    "slashing_db_up",
    "jdbc_connections",
)

# A metric selector: the name immediately preceding a label matcher.
METRIC_SELECTOR = re.compile(r"\b([a-z][a-z0-9_]*)\{")


def load_signer_dashboard() -> dict:
    with (LOCAL_APPS / "signer-slashing-dashboard.yaml").open(encoding="utf-8") as stream:
        config_map = yaml.safe_load(stream)
    return json.loads(next(iter(config_map["data"].values())))


class SignerScrapeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        with (SIGNER_BASE / "podmonitor.yaml").open(encoding="utf-8") as stream:
            self.podmonitor = yaml.safe_load(stream)
        with (LOCAL_APPS / "signer-telemetry-patch.yaml").open(encoding="utf-8") as stream:
            self.patch = yaml.safe_load(stream)

    def _labels(self, document: dict) -> dict[str, str]:
        endpoint = document["spec"]["podMetricsEndpoints"][0]
        return {r["targetLabel"]: r["replacement"] for r in endpoint.get("relabelings", [])}

    def test_base_sets_only_environment_independent_labels(self) -> None:
        """cluster/environment differ per environment and must not be baked into base."""
        labels = self._labels(self.podmonitor)
        self.assertEqual(labels, {"platform": "ethereum-validator", "component": "signer"})

    def test_local_patch_adds_the_environment_labels(self) -> None:
        labels = self._labels(self.patch)
        self.assertEqual(labels.get("cluster"), "kind-eth-validator-local")
        self.assertEqual(labels.get("environment"), "local")
        self.assertEqual(labels.get("component"), "signer")

    def test_signer_scrape_carries_no_validator_identity(self) -> None:
        """The signer tier is shared; per-validator labels there would be wrong."""
        for document in (self.podmonitor, self.patch):
            labels = self._labels(document)
            for forbidden in ("validator_id", "assignment_id", "customer_id"):
                self.assertNotIn(forbidden, labels)


class SignerRecordingRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        with (SIGNER_BASE / "prometheusrule.yaml").open(encoding="utf-8") as stream:
            self.rule = yaml.safe_load(stream)
        self.rules = self.rule["spec"]["groups"][0]["rules"]

    def test_records_exactly_the_declared_normalizations(self) -> None:
        self.assertEqual({r["record"] for r in self.rules}, SIGNER_RECORDING_RULES)

    def test_every_source_metric_is_verified(self) -> None:
        """Guard against inventing a Web3Signer signal."""
        allowed = VERIFIED_WEB3SIGNER_METRICS | {"up"}
        for rule in self.rules:
            referenced = set(METRIC_SELECTOR.findall(rule["expr"]))
            with self.subTest(record=rule["record"]):
                self.assertTrue(referenced, "rule must read a real source metric")
                self.assertTrue(
                    referenced.issubset(allowed),
                    f"unverified source metric(s): {sorted(referenced - allowed)}",
                )

    def test_rules_retain_environment_labels(self) -> None:
        for rule in self.rules:
            with self.subTest(record=rule["record"]):
                self.assertIn("by (cluster, environment)", rule["expr"])

    def test_rules_select_the_signer_component(self) -> None:
        for rule in self.rules:
            with self.subTest(record=rule["record"]):
                self.assertIn('component="signer"', rule["expr"])
                self.assertIn('platform="ethereum-validator"', rule["expr"])


class SignerDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard = load_signer_dashboard()
        self.expressions = [
            target["expr"]
            for panel in self.dashboard["panels"]
            for target in panel.get("targets", [])
            if "expr" in target
        ]

    def test_dashboard_identity(self) -> None:
        self.assertEqual(self.dashboard["uid"], SIGNER_DASHBOARD_UID)
        self.assertFalse(self.dashboard["editable"])
        ids = [panel["id"] for panel in self.dashboard["panels"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_metric_referenced_is_verified_or_normalized(self) -> None:
        allowed = VERIFIED_CNPG_METRICS | SIGNER_RECORDING_RULES
        for expression in self.expressions:
            referenced = set(METRIC_SELECTOR.findall(expression))
            with self.subTest(expr=expression[:60]):
                self.assertTrue(
                    referenced.issubset(allowed),
                    f"unverified metric(s): {sorted(referenced - allowed)}",
                )

    def test_no_invented_signer_database_metric(self) -> None:
        """Web3Signer exposes no DB or pool metric; a panel claiming one is fabricated."""
        payload = json.dumps(self.dashboard).lower()
        for pattern in FORBIDDEN_SIGNER_DB_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, payload)

    def test_panels_declare_an_explicit_no_value_state(self) -> None:
        for panel in self.dashboard["panels"]:
            if panel["type"] == "text":
                continue
            with self.subTest(panel=panel["title"]):
                self.assertTrue(panel["fieldConfig"]["defaults"].get("noValue"))

    def test_dashboard_states_the_signer_database_gap(self) -> None:
        text = " ".join(
            f"{p.get('title', '')} {p['options']['content']}"
            for p in self.dashboard["panels"]
            if p["type"] == "text"
        ).lower()
        self.assertIn("not collected", text)
        self.assertIn("not exposed by web3signer", text)
        # The fail-closed expectation must be stated, not assumed.
        self.assertIn("zero", text)

    def test_dashboard_carries_no_public_key_dimension(self) -> None:
        payload = json.dumps(self.dashboard)
        self.assertIsNone(re.search(r"0x[0-9a-fA-F]{96}", payload))
        for forbidden in ("pubkey", "public_key", "publickey"):
            self.assertNotIn(forbidden, payload.lower())


if __name__ == "__main__":
    unittest.main()
