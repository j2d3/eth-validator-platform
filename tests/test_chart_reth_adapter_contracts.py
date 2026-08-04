"""Offline chart-render contract for the Reth execution-client adapter.

Proves the chart accepts `executionClient: reth`, uses the Reth helpers
end-to-end (init command writes /data/db, run command execs `reth node`
with the right flags), and preserves the Geth path unchanged. Does NOT
prove any catalog/projection integration — that lives in a separate
serviceProfile + assignment + projection-tool PR.
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
        ["helm", "template", "reth-test", str(CHART), "--values", values_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"helm template failed: {result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def reth_ephemery_values() -> dict:
    return {
        "lifecycleState": "active",
        "executionClient": "reth",
        "consensusClient": "lighthouse",
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
                # A profile that offers a Reth pair must declare a Reth adapter.
                # Geth is still required by the current schema; all share mode.
                # Explicit `network: null` overrides the default `network: hoodi`
                # from values.yaml so artifact-bundle validation passes (Helm
                # deep-merges rather than replaces sub-maps).
                "geth": {"mode": "artifact-bundle", "network": None},
                "reth": {"mode": "artifact-bundle", "network": None},
                "lighthouse": {"mode": "artifact-bundle", "network": None},
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
        "telemetry": {"cluster": "reth-test-cluster", "environment": "dev"},
    }


class RethAdapterRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = helm_template(reth_ephemery_values())
        self.by_kind: dict[str, list[dict]] = {}
        for d in self.documents:
            self.by_kind.setdefault(d["kind"], []).append(d)

    def test_execution_container_is_reth_image_with_helper_command(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = sts["spec"]["template"]["spec"]["containers"]
        execution = next(c for c in containers if c["name"] == "execution")

        self.assertIn("paradigmxyz/reth", execution["image"])
        self.assertIn("@sha256:", execution["image"])
        # Helper-rendered command: shell wrap around exec reth node.
        self.assertEqual(execution["command"], ["/bin/sh", "-ec"])
        script = execution["args"][0]
        self.assertIn("exec reth node", script)
        self.assertIn("--chain ", script)
        self.assertIn("--datadir /data", script)
        self.assertIn("--authrpc.jwtsecret /jwt/jwt.hex", script)
        self.assertIn("--metrics 0.0.0.0:6060", script)
        # No Geth signature leaks in.
        self.assertNotIn("exec geth", script)
        self.assertNotIn("--http.vhosts", script)

    def test_init_container_uses_reth_naming_and_data_marker(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        init_containers = sts["spec"]["template"]["spec"]["initContainers"]
        genesis_init = next(
            ic for ic in init_containers if "initialize-" in ic["name"]
        )
        self.assertEqual(genesis_init["name"], "initialize-reth-genesis")
        script = genesis_init["args"][0]
        # Marker check specific to Reth's on-disk layout.
        self.assertIn("test -d /data/db", script)
        self.assertNotIn("test -d /data/geth", script)
        self.assertIn("reth init --chain ", script)

    def test_geth_ephemery_still_renders_unchanged(self) -> None:
        # Flip the same profile to Geth and confirm we get the Geth shell wrap.
        values = reth_ephemery_values()
        values["executionClient"] = "geth"
        documents = helm_template(values)
        sts = next(d for d in documents if d["kind"] == "StatefulSet")
        execution = next(
            c for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "execution"
        )
        self.assertIn("ethereum/client-go", execution["image"])
        script = execution["args"][0]
        self.assertIn("exec geth", script)
        init = next(
            ic for ic in sts["spec"]["template"]["spec"]["initContainers"]
            if "initialize-" in ic["name"]
        )
        self.assertEqual(init["name"], "initialize-geth-genesis")


if __name__ == "__main__":
    unittest.main()
