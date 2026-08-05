"""Offline chart-render contract for the Nimbus consensus-client adapter.

Proves the chart accepts `consensusClient: nimbus`, uses the Nimbus helpers
end-to-end (run command execs `nimbus_beacon_node` with Nimbus-native
flags), and preserves the Lighthouse path unchanged. Does NOT prove any
catalog/projection integration or runtime metric-name accuracy — those live
in a separate serviceProfile + assignment + runtime-verify PR.
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
        ["helm", "template", "nimbus-test", str(CHART), "--values", values_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"helm template failed: {result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def nimbus_ephemery_values() -> dict:
    return {
        "lifecycleState": "active",
        "executionClient": "geth",
        "consensusClient": "nimbus",
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
                "nimbus": {"mode": "artifact-bundle", "network": None},
            },
            "artifactBundle": {
                "url": "https://example.invalid/testnet-all.tar.gz",
                "sha256": "0" * 64,
                "files": {
                    "executionGenesis": "genesis.json",
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
        "telemetry": {"cluster": "nimbus-test-cluster", "environment": "dev"},
    }


class NimbusAdapterRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = helm_template(nimbus_ephemery_values())
        self.by_kind: dict[str, list[dict]] = {}
        for d in self.documents:
            self.by_kind.setdefault(d["kind"], []).append(d)

    def test_consensus_container_is_nimbus_image_with_helper_command(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = sts["spec"]["template"]["spec"]["containers"]
        consensus = next(c for c in containers if c["name"] == "consensus")

        self.assertIn("statusim/nimbus-eth2", consensus["image"])
        self.assertIn("@sha256:", consensus["image"])
        self.assertEqual(consensus["command"], ["/bin/sh", "-ec"])
        script = consensus["args"][0]
        self.assertIn("exec /home/user/nimbus_beacon_node", script)
        # Nimbus takes the network directory (not a single file) via --network.
        self.assertIn("--network=/network/files", script)
        # Checkpoint-adjacent BN wiring at normal startup uses
        # --external-beacon-api-url. --trusted-node-url is accepted only by
        # the one-shot `trustedNodeSync` subcommand and would exit the normal
        # beacon start immediately with "Unrecognized option".
        self.assertIn(
            "--external-beacon-api-url=https://checkpoint-sync.example.invalid/",
            script,
        )
        for forbidden in ("--trusted-node-url", "trustedNodeSync"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script)
        # Bootstrap file must be line-delimited ENRs (.txt), not YAML. This
        # Nimbus release exits with "Unknown bootstrap file format" for
        # boot_enr.yaml because the file contains YAML list markers.
        self.assertIn(
            "--bootstrap-file=/network/files/boot_enr.txt", script
        )
        self.assertNotIn("boot_enr.yaml", script)
        # Data path and EE wiring. Current Nimbus uses --el; --web3-url is a
        # hidden legacy alias and is intentionally not emitted.
        # Kubernetes mounts the PVC root as root-owned/fsGroup-writable. Nimbus
        # insists its data directory itself be 0700 and a hardened non-root
        # process cannot chmod the mount root, so use a Nimbus-owned child.
        self.assertIn("--data-dir=/data/nimbus", script)
        self.assertIn("--el=http://127.0.0.1:8551", script)
        self.assertNotIn("--web3-url", script)
        self.assertIn("--jwt-secret=/jwt/jwt.hex", script)
        # REST + metrics on the standard ports the chart's PodMonitor scrapes.
        self.assertIn("--rest-address=0.0.0.0", script)
        self.assertIn("--rest-port=5052", script)
        self.assertIn("--metrics-port=8008", script)
        # No Lighthouse or Teku signatures leak in.
        self.assertNotIn("exec lighthouse", script)
        self.assertNotIn("exec /opt/teku/bin/teku", script)

    def test_consensus_container_uses_nimbus_flag_shape_not_lighthouse(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        consensus = next(
            c for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        script = consensus["args"][0]
        # Nimbus uses --data-dir (Lighthouse uses --datadir).
        self.assertIn("--data-dir=/data/nimbus", script)
        self.assertNotIn("--data-dir=/data ", script)
        self.assertNotIn("--datadir=/data", script)
        # Nimbus uses --el for the EE (Lighthouse uses --execution-endpoint).
        self.assertIn("--el=", script)
        self.assertNotIn("--execution-endpoint=", script)
        # Nimbus uses --jwt-secret (Lighthouse uses --execution-jwt).
        self.assertIn("--jwt-secret=/jwt/jwt.hex", script)
        self.assertNotIn("--execution-jwt=", script)

    def test_prometheusrule_unions_nimbus_consensus_metrics(self) -> None:
        rule = next(
            d for d in self.documents if d["kind"] == "PrometheusRule"
        )
        rules = rule["spec"]["groups"][0]["rules"]
        by_record = {r["record"]: r["expr"] for r in rules}
        peers_expr = by_record["validator_platform_consensus_peers"]
        self.assertIn("libp2p_peers", peers_expr)
        self.assertIn('consensus_client="nimbus"', peers_expr)

    def test_lighthouse_ephemery_still_renders_unchanged(self) -> None:
        # Flip the same profile back to Lighthouse and confirm the Lighthouse
        # command still renders identically.
        values = nimbus_ephemery_values()
        values["consensusClient"] = "lighthouse"
        documents = helm_template(values)
        sts = next(d for d in documents if d["kind"] == "StatefulSet")
        consensus = next(
            c for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        script = consensus["args"][0]
        self.assertIn("exec lighthouse bn", script)
        self.assertIn("--testnet-dir=/network/files", script)
        self.assertNotIn("nimbus", script)


if __name__ == "__main__":
    unittest.main()
