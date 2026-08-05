"""Offline chart-render contract for the Nethermind execution-client adapter.

Proves the chart accepts `executionClient: nethermind`, uses the Nethermind
helpers end-to-end (init creates the /data/nethermind_db marker + keystore
subdir, run command execs Nethermind with the runtime-verified flag shape:
`--config=none --Init.ChainSpecPath=... --KeyStore.KeyStoreDirectory=...`),
and preserves the Geth path unchanged. Does NOT prove any catalog/projection
integration or runtime metric-name accuracy — those live in a separate
serviceProfile + assignment + runtime-verify PR, following the same
"configured but runtime-unverified" pattern from Besu (#137) and the Erigon
`chain_head_block` gap fixed by observation in #148.
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
        ["helm", "template", "nethermind-test", str(CHART), "--values", values_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"helm template failed: {result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def nethermind_ephemery_values() -> dict:
    return {
        "lifecycleState": "active",
        "executionClient": "nethermind",
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
                # A profile that offers a Nethermind pair must declare a
                # Nethermind adapter. Geth is still required by the current
                # schema; all declared clients share mode. `network: null`
                # clears the values.yaml default so artifact-bundle
                # validation passes.
                "geth": {"mode": "artifact-bundle", "network": None},
                "nethermind": {"mode": "artifact-bundle", "network": None},
                "lighthouse": {"mode": "artifact-bundle", "network": None},
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
        "telemetry": {"cluster": "nethermind-test-cluster", "environment": "dev"},
    }


class NethermindAdapterRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = helm_template(nethermind_ephemery_values())
        self.by_kind: dict[str, list[dict]] = {}
        for d in self.documents:
            self.by_kind.setdefault(d["kind"], []).append(d)

    def test_execution_container_is_nethermind_image_with_helper_command(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = sts["spec"]["template"]["spec"]["containers"]
        execution = next(c for c in containers if c["name"] == "execution")

        self.assertIn("nethermind/nethermind", execution["image"])
        self.assertIn("@sha256:", execution["image"])
        self.assertEqual(execution["command"], ["/bin/sh", "-ec"])
        script = execution["args"][0]

        # Nethermind uses its own Nethermind.Runner entrypoint, not
        # a `nethermind` alias.
        self.assertIn("exec ./Nethermind.Runner", script)
        # --config=none disables built-in network selection so the runtime
        # is driven entirely by --Init.ChainSpecPath.
        self.assertIn("--config=none", script)
        # Uses the Nethermind-format chainspec from the bundle, not
        # geth-style genesis.json. Negative-space: no `.json` reference on
        # the argv side other than chainspec.json (the comment can mention
        # genesis.json narratively).
        self.assertIn(
            "--Init.ChainSpecPath=/network/files/chainspec.json", script
        )
        for line in script.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn("genesis.json", stripped)
        # Data path lives on the shared /data PVC; keystore pinned into the
        # PVC because the default /nethermind/keystore/node.key.plain path
        # fails under the chart's read-only-root Pod contract.
        self.assertIn("--Init.BaseDbPath=/data", script)
        self.assertIn("--KeyStore.KeyStoreDirectory=/data/keystore", script)
        # JSON-RPC + Engine API + metrics wiring.
        self.assertIn("--JsonRpc.Enabled=true", script)
        self.assertIn("--JsonRpc.Port=8545", script)
        self.assertIn("--JsonRpc.EnginePort=8551", script)
        self.assertIn("--JsonRpc.JwtSecretFile=/jwt/jwt.hex", script)
        self.assertIn("--Metrics.Enabled=true", script)
        self.assertIn("--Metrics.ExposePort=6060", script)
        # Neither Geth, Reth, Erigon, nor Besu signatures leak in.
        self.assertNotIn("exec geth", script)
        self.assertNotIn("exec reth", script)
        self.assertNotIn("exec erigon", script)
        self.assertNotIn("exec besu", script)

    def test_podmonitor_uses_nethermind_metrics_path(self) -> None:
        # Nethermind exposes Prometheus at `/metrics`, not Geth's
        # `/debug/metrics/prometheus`. Runtime-verified by Codex against
        # the pinned 1.39.2 image on 2026-08-05: `/metrics` returned
        # HTTP 200 with `nethermind_*` series; `/` redirected.
        monitor = self.by_kind["PodMonitor"][0]
        execution = next(
            endpoint
            for endpoint in monitor["spec"]["podMetricsEndpoints"]
            if endpoint["port"] == "el-metrics"
        )
        self.assertEqual(execution["path"], "/metrics")

    def test_init_container_uses_nethermind_naming_and_data_marker(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        init_containers = sts["spec"]["template"]["spec"]["initContainers"]
        genesis_init = next(
            ic for ic in init_containers if "initialize-" in ic["name"]
        )
        self.assertEqual(genesis_init["name"], "initialize-nethermind-genesis")
        script = genesis_init["args"][0]
        # Marker check specific to Nethermind's on-disk layout. A marker-only
        # claim (or marker + /data/keystore only) is resumable because Pod
        # replacement can happen between the marker write and Nethermind's
        # first DB creation on Spot capacity.
        self.assertIn("if [ ! -d /data/nethermind_db ]; then", script)
        self.assertIn('[ "$entry" = "$marker" ] && continue', script)
        self.assertIn('[ "$entry" = /data/keystore ] && continue', script)
        self.assertNotIn("test -d /data/geth", script)
        self.assertNotIn("test -d /data/db", script)
        self.assertNotIn("test -d /data/chaindata", script)
        self.assertNotIn("test -d /data/database", script)
        # Nethermind has no init subcommand. It must create its own DB
        # directory; a pre-created /data/nethermind_db could be interpreted
        # by Nethermind as an existing DB with missing metadata (same
        # failure class Besu had in #155).
        for line in script.splitlines():
            normalized = line.strip().replace('"', "").replace("'", "")
            self.assertFalse(
                normalized.startswith(
                    (
                        "mkdir /data/nethermind_db",
                        "mkdir -p /data/nethermind_db",
                    )
                ),
                msg="init script must leave Nethermind database creation to Nethermind",
            )
        # Keystore is different: Nethermind's --KeyStore.KeyStoreDirectory
        # points at /data/keystore, and Nethermind refuses to start if that
        # path doesn't exist. The helper creates it, but only after writing
        # the platform marker so an interrupted init is resumable.
        self.assertIn("mkdir -p /data/keystore", script)

    def test_init_state_machine_handles_interrupted_first_start(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        init_containers = sts["spec"]["template"]["spec"]["initContainers"]
        genesis_init = next(
            ic for ic in init_containers if ic["name"] == "initialize-nethermind-genesis"
        )
        rendered_script = genesis_init["args"][0]
        fingerprint = nethermind_ephemery_values()["networkProfile"][
            "identityFingerprint"
        ]

        def run(state: str) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data = root / "data"
                network = root / "network"
                data.mkdir()
                network.mkdir()
                (network / ".verified-identity").write_text(
                    fingerprint, encoding="utf-8"
                )
                marker = data / ".platform-network-identity"
                if state == "empty":
                    pass
                elif state == "interrupted-before-keystore":
                    # Simulate a Pod replacement between the marker write and
                    # the keystore mkdir: only the marker is present.
                    marker.write_text(fingerprint, encoding="utf-8")
                elif state == "interrupted-before-db":
                    # Marker + keystore present; Nethermind hasn't yet
                    # created its own DB directory.
                    marker.write_text(fingerprint, encoding="utf-8")
                    (data / "keystore").mkdir()
                elif state == "initialized":
                    marker.write_text(fingerprint, encoding="utf-8")
                    (data / "keystore").mkdir()
                    (data / "nethermind_db").mkdir()
                elif state == "wrong-network":
                    marker.write_text("f" * 64, encoding="utf-8")
                elif state == "unrelated-data":
                    marker.write_text(fingerprint, encoding="utf-8")
                    (data / "someone-else").write_text("x", encoding="utf-8")
                elif state == "foreign-unmarked":
                    (data / "someone-else").write_text("x", encoding="utf-8")
                else:
                    raise AssertionError(f"unknown state {state!r}")
                # Path-substitute so /data and /network target the temp dirs.
                # Sentinel-then-replace avoids the /data-inside-/data/keystore
                # double-rewrite failure mode.
                script = (
                    rendered_script.replace("/data/", "__DATA_ROOT__/")
                    .replace("/network/", "__NETWORK_ROOT__/")
                    .replace("__DATA_ROOT__", str(data))
                    .replace("__NETWORK_ROOT__", str(network))
                )
                return subprocess.run(
                    ["/bin/sh", "-ec", script],
                    check=False,
                    capture_output=True,
                    text=True,
                )

        for state in (
            "empty",
            "interrupted-before-keystore",
            "interrupted-before-db",
            "initialized",
        ):
            with self.subTest(state=state):
                result = run(state)
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"state={state!r}: unexpectedly rejected. stderr={result.stderr}",
                )
        for state in ("wrong-network", "unrelated-data", "foreign-unmarked"):
            with self.subTest(state=state):
                result = run(state)
                self.assertNotEqual(
                    result.returncode,
                    0,
                    msg=f"state={state!r}: should have been rejected. stdout={result.stdout}",
                )

    def test_geth_ephemery_still_renders_unchanged(self) -> None:
        # Flip the same profile to Geth and confirm we get the Geth shell wrap.
        values = nethermind_ephemery_values()
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
