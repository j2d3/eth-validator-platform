"""Offline chart-render contract for the Besu execution-client adapter.

Proves the chart accepts `executionClient: besu`, uses the Besu helpers
end-to-end (init creates /data/database marker, run command execs `besu`
with the right hyphen-flag shape), and preserves the Geth path unchanged.
Does NOT prove any catalog/projection integration or runtime metric-name
accuracy — those live in a separate serviceProfile + assignment + runtime-
verify PR.
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
        ["helm", "template", "besu-test", str(CHART), "--values", values_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"helm template failed: {result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def besu_ephemery_values() -> dict:
    return {
        "lifecycleState": "active",
        "executionClient": "besu",
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
                # A profile that offers a Besu pair must declare a Besu adapter.
                # Geth is still required by the current schema; all declared
                # clients share mode. `network: null` clears the values.yaml
                # default so artifact-bundle validation passes.
                "geth": {"mode": "artifact-bundle", "network": None},
                "besu": {"mode": "artifact-bundle", "network": None},
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
        "telemetry": {"cluster": "besu-test-cluster", "environment": "dev"},
    }


class BesuAdapterRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = helm_template(besu_ephemery_values())
        self.by_kind: dict[str, list[dict]] = {}
        for d in self.documents:
            self.by_kind.setdefault(d["kind"], []).append(d)

    def test_execution_container_is_besu_image_with_helper_command(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = sts["spec"]["template"]["spec"]["containers"]
        execution = next(c for c in containers if c["name"] == "execution")

        self.assertIn("hyperledger/besu", execution["image"])
        self.assertIn("@sha256:", execution["image"])
        self.assertEqual(execution["command"], ["/bin/sh", "-ec"])
        script = execution["args"][0]
        self.assertIn("exec besu", script)
        # Besu uses --data-path (not --datadir).
        self.assertIn("--data-path=/data", script)
        # Besu takes the digest-verified genesis via --genesis-file.
        self.assertIn("--genesis-file=/network/files/genesis.json", script)
        self.assertIn("--bootnodes=", script)
        # RPC and Engine API flags use Besu's hyphen convention.
        self.assertIn("--rpc-http-enabled", script)
        self.assertIn("--rpc-http-port=8545", script)
        self.assertIn("--engine-rpc-enabled", script)
        self.assertIn("--engine-jwt-secret=/jwt/jwt.hex", script)
        self.assertIn("--metrics-enabled", script)
        self.assertIn("--metrics-port=6060", script)
        # Neither Geth nor Reth nor Erigon signatures leak in.
        self.assertNotIn("exec geth", script)
        self.assertNotIn("exec reth", script)
        self.assertNotIn("exec erigon", script)

    def test_init_container_uses_besu_naming_and_data_marker(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        init_containers = sts["spec"]["template"]["spec"]["initContainers"]
        genesis_init = next(
            ic for ic in init_containers if "initialize-" in ic["name"]
        )
        self.assertEqual(genesis_init["name"], "initialize-besu-genesis")
        script = genesis_init["args"][0]
        # Marker check specific to Besu's on-disk layout.
        self.assertIn("test -d /data/database", script)
        self.assertNotIn("test -d /data/geth", script)
        self.assertNotIn("test -d /data/db", script)
        self.assertNotIn("test -d /data/chaindata", script)
        # Besu has no `besu init` subcommand; the helper preemptively
        # creates the marker directory for restart idempotence. Assert the
        # mkdir shape is present and that no line executes `besu init`.
        self.assertIn("mkdir -p /data/database", script)
        for line in script.splitlines():
            with self.subTest(line=line):
                self.assertFalse(
                    line.lstrip().startswith("besu init"),
                    msg="init script must not invoke `besu init` (no such subcommand)",
                )

    def test_geth_ephemery_still_renders_unchanged(self) -> None:
        # Flip the same profile to Geth and confirm we get the Geth shell wrap.
        values = besu_ephemery_values()
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


class BesuTekuCompositionRenderTests(unittest.TestCase):
    """Renders the actual Besu + Teku composition end-to-end.

    Neither the Besu-only nor the Teku-only adapter tests exercise
    dispatch when both adapters fire simultaneously. This test builds
    the composed values, renders through helm, and asserts the resulting
    StatefulSet contains both the Besu execution command/image and the
    Teku consensus command/image — the layer where the catalog #149
    activation actually depends on the chart doing the right thing.
    """

    @classmethod
    def setUpClass(cls) -> None:
        values = besu_ephemery_values()
        values["consensusClient"] = "teku"
        values["networkProfile"]["clients"]["teku"] = {
            "mode": "artifact-bundle",
            "network": None,
        }
        cls.documents = helm_template(values)
        cls.by_kind: dict[str, list[dict]] = {}
        for d in cls.documents:
            cls.by_kind.setdefault(d["kind"], []).append(d)

    def test_stateful_set_contains_both_besu_and_teku_containers(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = {
            c["name"]: c for c in sts["spec"]["template"]["spec"]["containers"]
        }
        self.assertIn("execution", containers)
        self.assertIn("consensus", containers)

        execution = containers["execution"]
        self.assertIn("hyperledger/besu", execution["image"])
        self.assertIn("@sha256:", execution["image"])
        exec_script = execution["args"][0]
        self.assertIn("exec besu", exec_script)
        self.assertIn("--engine-jwt-secret=/jwt/jwt.hex", exec_script)

        consensus = containers["consensus"]
        self.assertIn("consensys/teku", consensus["image"])
        self.assertIn("@sha256:", consensus["image"])
        cons_script = consensus["args"][0]
        self.assertIn("exec /opt/teku/bin/teku", cons_script)
        # Teku connects to Besu's Engine API via the shared JWT and the
        # same intra-Pod localhost endpoint the Geth+Teku pair uses.
        self.assertIn("--ee-endpoint=http://127.0.0.1:8551", cons_script)
        self.assertIn("--ee-jwt-secret-file=/jwt/jwt.hex", cons_script)

    def test_composition_does_not_leak_other_client_signatures(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = {
            c["name"]: c for c in sts["spec"]["template"]["spec"]["containers"]
        }
        exec_script = containers["execution"]["args"][0]
        cons_script = containers["consensus"]["args"][0]
        # Execution container is Besu, not any other EL.
        for other_el in ("exec geth", "exec reth", "exec erigon"):
            self.assertNotIn(other_el, exec_script)
        # Consensus container is Teku, not Lighthouse or Nimbus.
        self.assertNotIn("exec lighthouse", cons_script)
        self.assertNotIn("exec /home/user/nimbus_beacon_node", cons_script)

    def test_besu_teku_init_container_uses_besu_marker(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        init = next(
            ic for ic in sts["spec"]["template"]["spec"]["initContainers"]
            if "initialize-" in ic["name"]
        )
        self.assertEqual(init["name"], "initialize-besu-genesis")


if __name__ == "__main__":
    unittest.main()
