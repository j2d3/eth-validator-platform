"""Offline chart-render contract for the Erigon execution-client adapter.

Proves the chart accepts `executionClient: erigon`, uses the Erigon helpers
end-to-end (init writes to /data/chaindata, run command execs `erigon`
with the right flags), and preserves the Geth path unchanged. Does NOT
prove any catalog/projection integration or runtime metric-name accuracy —
those live in a separate serviceProfile + assignment + runtime-verify PR.
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
        ["helm", "template", "erigon-test", str(CHART), "--values", values_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"helm template failed: {result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def erigon_ephemery_values() -> dict:
    return {
        "lifecycleState": "active",
        "executionClient": "erigon",
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
                # A profile that offers an Erigon pair must declare an Erigon
                # adapter. Geth is still required by the current schema; all
                # declared clients share mode. `network: null` clears the
                # values.yaml default so artifact-bundle validation passes.
                "geth": {"mode": "artifact-bundle", "network": None},
                "erigon": {"mode": "artifact-bundle", "network": None},
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
        "telemetry": {"cluster": "erigon-test-cluster", "environment": "dev"},
    }


class ErigonAdapterRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = helm_template(erigon_ephemery_values())
        self.by_kind: dict[str, list[dict]] = {}
        for d in self.documents:
            self.by_kind.setdefault(d["kind"], []).append(d)

    def test_execution_container_is_erigon_image_with_helper_command(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = sts["spec"]["template"]["spec"]["containers"]
        execution = next(c for c in containers if c["name"] == "execution")

        self.assertIn("erigontech/erigon", execution["image"])
        self.assertIn("@sha256:", execution["image"])
        # Helper-rendered command: shell wrap around exec erigon.
        self.assertEqual(execution["command"], ["/bin/sh", "-ec"])
        script = execution["args"][0]
        self.assertIn("exec erigon", script)
        self.assertIn("--datadir=/data", script)
        self.assertIn("--bootnodes=", script)
        self.assertIn("--http", script)
        self.assertIn("--authrpc.jwtsecret=/jwt/jwt.hex", script)
        self.assertIn("--metrics.port=6060", script)
        # No Geth signature leaks in.
        self.assertNotIn("exec geth", script)
        # No Reth signature leaks in.
        self.assertNotIn("exec reth", script)

    def test_init_container_uses_erigon_naming_and_data_marker(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        init_containers = sts["spec"]["template"]["spec"]["initContainers"]
        genesis_init = next(
            ic for ic in init_containers if "initialize-" in ic["name"]
        )
        self.assertEqual(genesis_init["name"], "initialize-erigon-genesis")
        script = genesis_init["args"][0]
        # Marker check specific to Erigon's on-disk layout.
        self.assertIn("test -d /data/chaindata", script)
        self.assertNotIn("test -d /data/geth", script)
        self.assertNotIn("test -d /data/db", script)
        # Erigon init subcommand takes datadir + genesis file positionally.
        self.assertIn("erigon init", script)

    def test_geth_ephemery_still_renders_unchanged(self) -> None:
        # Flip the same profile to Geth and confirm we get the Geth shell wrap.
        values = erigon_ephemery_values()
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
