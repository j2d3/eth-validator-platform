"""Offline chart-render contract for the Prysm consensus-client adapter.

Proves the chart accepts `consensusClient: prysm`, uses the Prysm helpers
end-to-end (run command execs `/beacon-chain` with Prysm-native flags and
performs the Ephemery config.yaml -> prysm-config.yaml derivation before
exec), and preserves the Lighthouse path unchanged. Does NOT prove any
catalog/projection integration or runtime metric-name accuracy -- those
live in a separate serviceProfile + assignment + runtime-verify PR.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "ethereum-node"


def helm_template(values: dict) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(values, f, sort_keys=False)
        values_path = f.name
    result = subprocess.run(
        ["helm", "template", "prysm-test", str(CHART), "--values", values_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"helm template failed: {result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def prysm_ephemery_values() -> dict:
    return {
        "lifecycleState": "active",
        "executionClient": "geth",
        "consensusClient": "prysm",
        "networkProfile": {
            "name": "ephemery-test",
            "family": "ephemery",
            "generation": "162",
            "resetPolicy": "replace-data",
            "identityFingerprint":
                "1607eeafd1831115cd81bfd3aed07ea9a154ec688776a25f3395c960756a048c",
            "identity": {
                "executionChainId": 39438162,
                "executionNetworkId": 39438162,
                "executionGenesisHash":
                    "0x7398e3663283d8dbbf92bfdfee7dedd311079fb131ec1ce2501d5b7043f3d7a9",
                "consensusGenesisValidatorsRoot":
                    "0xe7ba535e068e129a2e3b17ee6a8f275eee3d1a01126f583ea7b6e867a91c0e5e",
                "genesisForkVersion": "0x1000101b",
                "consensusGenesisTime": 1785438600,
            },
            "clients": {
                "geth": {"mode": "artifact-bundle", "network": None},
                "lighthouse": {"mode": "artifact-bundle", "network": None},
                "prysm": {"mode": "artifact-bundle", "network": None},
            },
            "artifactBundle": {
                "url": "https://example.invalid/testnet-all.tar.gz",
                "sha256": "0" * 64,
                "files": {
                    "executionGenesis": "genesis.json",
                    "executionChainspec": "chainspec.json",
                    "consensusConfig": "config.yaml",
                    "consensusGenesis": "genesis.ssz",
                    "consensusGenesisValidatorsRoot":
                        "genesis_validators_root.txt",
                    "executionBootnodes": "enodes.txt",
                    "executionBootnode": "boot_enode.txt",
                    "consensusBootnodes": "boot_enr.yaml",
                    "consensusBootnodesText": "boot_enr.txt",
                    "nodeVariables": "nodevars_env.txt",
                    "depositContract": "deposit_contract.txt",
                    "depositContractBlock": "deposit_contract_block.txt",
                    "depositContractBlockHash":
                        "deposit_contract_block_hash.txt",
                    "retentionVariables": "retention.vars",
                },
            },
            "checkpointSync": {
                "primaryUrl": "https://checkpoint-sync.example.invalid/",
                "verificationUrls":
                    ["https://checkpoint-verify.example.invalid/"],
            },
        },
        "identity": {
            "customerId": "customer-test",
            "validatorId": "validator-test",
            "assignmentId": "assignment-test",
        },
        "telemetry": {"cluster": "prysm-test-cluster", "environment": "dev"},
    }


class PrysmAdapterRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = helm_template(prysm_ephemery_values())
        self.by_kind: dict[str, list[dict]] = {}
        for d in self.documents:
            self.by_kind.setdefault(d["kind"], []).append(d)

    def test_consensus_container_is_prysm_image_with_helper_command(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        consensus = next(
            c
            for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        self.assertIn("prysmaticlabs/prysm/beacon-chain", consensus["image"])
        self.assertIn("@sha256:", consensus["image"])
        script = consensus["args"][0]

        # Prysm-native execution binary. Reject the other CL exec patterns to
        # keep dispatcher regressions loud.
        self.assertIn("exec /beacon-chain", script)
        self.assertNotIn("exec lighthouse", script)
        self.assertNotIn("exec /opt/teku/bin/teku", script)
        self.assertNotIn("exec /home/user/nimbus_beacon_node", script)

        # Core Prysm flags per Codex runtime-verify against v7.1.8 on
        # 2026-08-05.
        self.assertIn("--chain-config-file=/tmp/prysm-config.yaml", script)
        self.assertIn(
            "--genesis-state=/network/files/genesis.ssz", script
        )
        self.assertIn("--datadir=/data/prysm", script)
        self.assertIn(
            "--execution-endpoint=http://127.0.0.1:8551", script
        )
        self.assertIn("--jwt-secret=/jwt/jwt.hex", script)
        self.assertIn(
            "--checkpoint-sync-url=https://checkpoint-sync.example.invalid/",
            script,
        )
        # P2P + monitoring surface.
        self.assertIn("--p2p-local-ip=0.0.0.0", script)
        self.assertIn("--p2p-tcp-port=9000", script)
        self.assertIn("--p2p-udp-port=9000", script)
        self.assertIn("--p2p-quic-port=9001", script)
        # REST API is exposed for oncall/scrape (0.0.0.0); gRPC stays on
        # loopback since no in-cluster consumer needs it.
        self.assertIn("--http-host=0.0.0.0", script)
        self.assertIn("--http-port=5052", script)
        self.assertIn("--rpc-host=127.0.0.1", script)
        self.assertIn("--monitoring-host=0.0.0.0", script)
        self.assertIn("--monitoring-port=8008", script)
        # Prysm requires this on non-mainnet networks.
        self.assertIn("--accept-terms-of-use", script)

    def test_config_derivation_accepts_ephemery_shape(self) -> None:
        # Take the derivation fragment (everything before `exec /beacon-chain`)
        # and run it against a realistic Ephemery config.yaml. Assert the
        # derived file has the two rejected keys stripped and the two Gloas
        # fork fields appended.
        sts = self.by_kind["StatefulSet"][0]
        consensus = next(
            c
            for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        script = consensus["args"][0]
        prefix, separator, _ = script.partition("exec /beacon-chain")
        self.assertTrue(separator)

        with tempfile.TemporaryDirectory() as directory:
            network_dir = Path(directory) / "network"
            network_dir.mkdir()
            (network_dir / "config.yaml").write_text(
                "PRESET_BASE: mainnet\n"
                "CONFIG_NAME: ephemery\n"
                "EPHEMERY_RESET_PERIOD: 604800\n"
                "NUMBER_OF_COLUMNS: 128\n"
                "GENESIS_FORK_VERSION: 0x1000101b\n"
                "ALTAIR_FORK_VERSION: 0x2000101b\n",
                encoding="utf-8",
            )
            (network_dir / "boot_enr.txt").write_text(
                "enr:-first\nenr:-second\n", encoding="utf-8"
            )
            tmp_root = Path(directory) / "tmp"
            tmp_root.mkdir()
            probe = (
                prefix.replace("/network/files/", str(network_dir) + "/")
                .replace("/tmp/prysm-config.yaml", str(tmp_root / "prysm-config.yaml"))
                + "printf DONE\n"
            )
            result = subprocess.run(
                ["/bin/sh", "-ec", probe],
                check=False,
                capture_output=True,
                text=True,
            )
            derived = (tmp_root / "prysm-config.yaml").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DONE", result.stdout)
        # Rejected keys stripped.
        self.assertNotIn("EPHEMERY_RESET_PERIOD", derived)
        self.assertNotIn("NUMBER_OF_COLUMNS", derived)
        # Unrelated content preserved.
        self.assertIn("PRESET_BASE: mainnet", derived)
        self.assertIn("GENESIS_FORK_VERSION: 0x1000101b", derived)
        # Gloas fork fields appended (avoids mainnet Gloas collision).
        self.assertIn("GLOAS_FORK_VERSION: 0x8000101b", derived)
        self.assertIn("GLOAS_FORK_EPOCH: 18446744073709551615", derived)

    def test_config_derivation_fails_closed_on_duplicate_gloas(self) -> None:
        # If upstream ever ships a bundle that already carries GLOAS_FORK_*
        # (i.e. the config shape drifted), the helper must fail-closed rather
        # than double-appending or silently keeping the upstream values --
        # so the operator has to explicitly review the new shape.
        sts = self.by_kind["StatefulSet"][0]
        consensus = next(
            c
            for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        script = consensus["args"][0]
        prefix, separator, _ = script.partition("exec /beacon-chain")
        self.assertTrue(separator)

        with tempfile.TemporaryDirectory() as directory:
            network_dir = Path(directory) / "network"
            network_dir.mkdir()
            (network_dir / "config.yaml").write_text(
                "PRESET_BASE: mainnet\n"
                "EPHEMERY_RESET_PERIOD: 604800\n"
                "NUMBER_OF_COLUMNS: 128\n"
                "GLOAS_FORK_VERSION: 0xdeadbeef\n",
                encoding="utf-8",
            )
            (network_dir / "boot_enr.txt").write_text(
                "enr:-first\n", encoding="utf-8"
            )
            tmp_root = Path(directory) / "tmp"
            tmp_root.mkdir()
            probe = (
                prefix.replace("/network/files/", str(network_dir) + "/")
                .replace("/tmp/prysm-config.yaml", str(tmp_root / "prysm-config.yaml"))
            )
            result = subprocess.run(
                ["/bin/sh", "-ec", probe],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GLOAS_FORK_", result.stderr)
        self.assertIn("refusing to append", result.stderr)

    def test_bootstrap_nodes_split_into_repeated_flags(self) -> None:
        # Prysm accepts repeated --bootstrap-node=<ENR>, NOT a single CSV
        # value (verified by Codex against v7.1.8). Extract the bootstrap
        # assembly fragment and prove it produces one flag per ENR.
        sts = self.by_kind["StatefulSet"][0]
        consensus = next(
            c
            for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        script = consensus["args"][0]
        prefix, separator, _ = script.partition("exec /beacon-chain")
        self.assertTrue(separator)

        with tempfile.TemporaryDirectory() as directory:
            network_dir = Path(directory) / "network"
            network_dir.mkdir()
            (network_dir / "config.yaml").write_text(
                "PRESET_BASE: mainnet\n"
                "EPHEMERY_RESET_PERIOD: 604800\n"
                "NUMBER_OF_COLUMNS: 128\n",
                encoding="utf-8",
            )
            (network_dir / "boot_enr.txt").write_text(
                "enr:-first\nenr:-second\nenr:-third\n", encoding="utf-8"
            )
            tmp_root = Path(directory) / "tmp"
            tmp_root.mkdir()
            probe = (
                prefix.replace("/network/files/", str(network_dir) + "/")
                .replace("/tmp/prysm-config.yaml", str(tmp_root / "prysm-config.yaml"))
                + "printf '%s\\n' \"$bootstrap_args\"\n"
            )
            result = subprocess.run(
                ["/bin/sh", "-ec", probe],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "--bootstrap-node=enr:-first --bootstrap-node=enr:-second --bootstrap-node=enr:-third",
        )
        # Reject the CSV shape defensively.
        self.assertNotIn("--bootstrap-node=enr:-first,enr:-second", script)

    def test_podmonitor_scrapes_prysm_metrics_port(self) -> None:
        pms = self.by_kind.get("PodMonitor", [])
        self.assertTrue(pms, "PodMonitor missing from render")
        endpoints = pms[0]["spec"]["podMetricsEndpoints"]
        consensus_ep = next(
            ep for ep in endpoints if ep.get("port") == "cl-metrics"
        )
        # Prysm exposes /metrics (same convention as the other CLs); the
        # chart's PodMonitor targets the named port `cl-metrics` which
        # maps to 8008 in the StatefulSet.
        self.assertEqual(consensus_ep.get("path", "/metrics"), "/metrics")

    def test_finality_lag_expr_derives_epoch_from_prysm_slot(self) -> None:
        # Prysm does not publish a direct present-epoch gauge; the recording
        # rule must compute it as floor(presentSlot / presentEpochDivisor).
        # This test locks in the derived-epoch code path against the exact
        # rendered expression so a refactor cannot silently reintroduce a
        # `presentEpoch` reference for Prysm (which would render an empty
        # series against the real scrape).
        rule = self.by_kind["PrometheusRule"][0]
        finality = next(
            item
            for group in rule["spec"]["groups"]
            for item in group["rules"]
            if item.get("record") == "validator_platform_consensus_finality_lag_epochs"
        )
        expr = finality["expr"]
        self.assertIn("floor(max by (", expr)
        self.assertIn(
            'beacon_clock_time_slot{platform="ethereum-validator",component="consensus",consensus_client="prysm"}',
            expr,
        )
        self.assertIn(") / 32) - max by (", expr)
        self.assertIn(
            'beacon_finalized_epoch{platform="ethereum-validator",component="consensus",consensus_client="prysm"}',
            expr,
        )
        # Direct-metric branches for the other CLs must remain intact.
        for direct_metric, cl in (
            ("slotclock_present_epoch", "lighthouse"),
            ("beacon_current_epoch", "nimbus"),
            ("beacon_epoch", "teku"),
        ):
            self.assertIn(
                f'{direct_metric}{{platform="ethereum-validator",component="consensus",consensus_client="{cl}"',
                expr,
            )


if __name__ == "__main__":
    unittest.main()
