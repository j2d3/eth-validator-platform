"""Contracts for Ethereum-pair and shared-signer runtime alerts."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "ethereum-node"
SIGNER_RULE = ROOT / "platform" / "apps" / "base" / "web3signer" / "prometheusrule.yaml"
RUNBOOK = ROOT / "docs" / "runbooks" / "ethereum-alerts.md"
PORTAL_REGISTRY = ROOT / "control-plane" / "portal" / "lib" / "portal-registry.ts"


def render_active_chart(*extra_args: str) -> list[dict]:
    result = subprocess.run(
        [
            "helm",
            "template",
            "alert-test",
            str(CHART),
            "--set",
            "lifecycleState=active",
            *extra_args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


class PairAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        documents = render_active_chart()
        rule = next(document for document in documents if document["kind"] == "PrometheusRule")
        group = next(group for group in rule["spec"]["groups"] if group["name"] == "ethereum-pair.alerts")
        cls.alerts = {alert["alert"]: alert for alert in group["rules"]}

    def test_alert_set_is_exact_and_uses_normalized_metrics(self) -> None:
        self.assertEqual(
            set(self.alerts),
            {
                "EthereumPairTargetUnavailable",
                "EthereumExecutionHeadStalled",
                "EthereumConsensusHeadStalled",
                "EthereumConsensusSlotLagHigh",
                "EthereumConsensusFinalityLagHigh",
            },
        )
        for alert in self.alerts.values():
            self.assertIn("validator_platform_", alert["expr"])
            self.assertEqual(alert["labels"]["severity"], "warning")
            self.assertEqual(alert["labels"]["assignment_id"], "assignment-synthetic-01")
            self.assertEqual(
                alert["annotations"]["runbook_url"],
                "https://github.com/j2d3/eth-validator-platform/blob/main/docs/runbooks/ethereum-alerts.md",
            )

    def test_missing_target_and_lag_thresholds_are_bounded(self) -> None:
        target = self.alerts["EthereumPairTargetUnavailable"]
        self.assertEqual(target["for"], "5m")
        self.assertIn("absent(", target["expr"])
        self.assertIn('component="execution"', target["expr"])
        self.assertIn('component="consensus"', target["expr"])

        slot = self.alerts["EthereumConsensusSlotLagHigh"]
        self.assertIn("> 8", slot["expr"])
        self.assertEqual(slot["for"], "5m")
        finality = self.alerts["EthereumConsensusFinalityLagHigh"]
        self.assertIn("> 4", finality["expr"])
        self.assertEqual(finality["for"], "10m")

    def test_stall_alerts_require_a_rolling_window_and_hold_time(self) -> None:
        for name in ("EthereumExecutionHeadStalled", "EthereumConsensusHeadStalled"):
            alert = self.alerts[name]
            self.assertIn("head_changes_15m", alert["expr"])
            self.assertIn("< 1", alert["expr"])
            self.assertEqual(alert["for"], "5m")

    def test_validator_target_alert_exists_only_for_signing_render(self) -> None:
        self.assertNotIn("EthereumValidatorTargetUnavailable", self.alerts)
        documents = render_active_chart(
            "--set",
            "validator.enabled=true",
            "--set",
            "validator.slashingProtectionConfirmed=true",
            "--set",
            "networkProfile.signer.web3signer.signingQualified=true",
            "--set",
            "validator.publicKey=0x" + "1" * 96,
            "--set",
            "validator.feeRecipient=0x" + "2" * 40,
        )
        rule = next(document for document in documents if document["kind"] == "PrometheusRule")
        group = next(group for group in rule["spec"]["groups"] if group["name"] == "ethereum-pair.alerts")
        alert = next(item for item in group["rules"] if item["alert"] == "EthereumValidatorTargetUnavailable")
        self.assertEqual(alert["labels"]["severity"], "critical")
        self.assertEqual(alert["for"], "2m")
        self.assertIn('component="validator"', alert["expr"])
        self.assertIn("absent(", alert["expr"])


class SignerAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rule = yaml.safe_load(SIGNER_RULE.read_text(encoding="utf-8"))
        group = next(group for group in rule["spec"]["groups"] if group["name"] == "web3signer.alerts")
        cls.alerts = {alert["alert"]: alert for alert in group["rules"]}

    def test_signer_alert_set_is_exact(self) -> None:
        self.assertEqual(
            set(self.alerts),
            {
                "Web3SignerUnavailable",
                "Web3SignerKeyCountBelowEnabledValidators",
                "Web3SignerSlashingCheckPrevented",
                "Web3SignerUnknownKeyRequest",
            },
        )

    def test_safety_counter_alerts_use_increase_not_absolute_totals(self) -> None:
        prevented = self.alerts["Web3SignerSlashingCheckPrevented"]
        self.assertEqual(prevented["labels"]["severity"], "critical")
        self.assertIn("increase(", prevented["expr"])
        self.assertIn("[5m]", prevented["expr"])

        unknown = self.alerts["Web3SignerUnknownKeyRequest"]
        self.assertEqual(unknown["labels"]["severity"], "warning")
        self.assertIn("increase(", unknown["expr"])

    def test_signer_availability_and_key_count_fail_closed(self) -> None:
        unavailable = self.alerts["Web3SignerUnavailable"]
        self.assertIn("absent(", unavailable["expr"])
        self.assertEqual(unavailable["for"], "2m")

        keys = self.alerts["Web3SignerKeyCountBelowEnabledValidators"]
        self.assertIn("validator_platform_validator_enabled", keys["expr"])
        self.assertIn("validator_platform_signer_keys_loaded", keys["expr"])
        self.assertEqual(keys["labels"]["severity"], "critical")


class AlertRunbookAndPortalTests(unittest.TestCase):
    def test_runbook_states_operational_and_safety_boundaries(self) -> None:
        text = " ".join(RUNBOOK.read_text(encoding="utf-8").split())
        for phrase in (
            "does not configure an external email, paging, or chat receiver",
            "Do not label Pod readiness as chain recovery",
            "do not bypass, truncate, recreate, or delete the RDS",
            "does not by itself prove zero missed duties",
        ):
            self.assertIn(phrase, text)

    def test_portal_links_to_the_real_grafana_alert_list(self) -> None:
        text = PORTAL_REGISTRY.read_text(encoding="utf-8")
        self.assertIn('name: "Alerts"', text)
        self.assertIn('href: `${grafanaBase}/alerting/list`', text)


if __name__ == "__main__":
    unittest.main()
