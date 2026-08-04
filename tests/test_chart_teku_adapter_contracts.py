"""Offline chart-render contract for the Teku consensus-client adapter.

Proves the chart accepts `consensusClient: teku`, uses the Teku helpers
end-to-end (run command execs `/opt/teku/bin/teku` with the expected
flags), and preserves the Lighthouse path unchanged. Does NOT prove any
catalog/projection integration, image-pin verification, or metric-map
runtime accuracy — those live in a separate serviceProfile + assignment
PR and are verified against a live Pod's /metrics output.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "ethereum-node"

# Synthetic pin used only to satisfy the chart's `@sha256:` schema pattern
# during render tests. The real Teku manifest digest is resolved by the
# assignment PR that first activates a Teku pair, following the same runtime
# verification discipline the Reth adapter used before shipping.
TEKU_TEST_IMAGE = (
    "consensys/teku:test@sha256:"
    "0000000000000000000000000000000000000000000000000000000000000000"
)


def helm_template(values: dict) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(values, f, sort_keys=False)
        values_path = f.name
    result = subprocess.run(
        ["helm", "template", "teku-test", str(CHART), "--values", values_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"helm template failed: {result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def teku_ephemery_values() -> dict:
    return {
        "lifecycleState": "active",
        "executionClient": "geth",
        "consensusClient": "teku",
        "consensusClients": {
            "teku": {
                "image": TEKU_TEST_IMAGE,
                "metrics": {
                    # Teku's native metric names. These names track Teku
                    # documented series but are unverified against a live
                    # Pod until the first Teku pair is scraped; the recording
                    # rules degrade to empty series for unknown names rather
                    # than render errors.
                    "headSlot": "beacon_head_slot",
                    "presentSlot": "beacon_slot",
                    "presentEpoch": "beacon_epoch",
                    "finalizedEpoch": "beacon_finalized_epoch",
                    "peers": "beacon_peer_count",
                },
            },
        },
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
                # Overriding `network: null` collapses the values.yaml default
                # `network: hoodi` so the artifact-bundle-only adapter schema
                # is satisfied.
                "geth": {"mode": "artifact-bundle", "network": None},
                "lighthouse": {"mode": "artifact-bundle", "network": None},
                "teku": {"mode": "artifact-bundle", "network": None},
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
        "telemetry": {"cluster": "teku-test-cluster", "environment": "dev"},
    }


class TekuAdapterRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = helm_template(teku_ephemery_values())
        self.by_kind: dict[str, list[dict]] = {}
        for d in self.documents:
            self.by_kind.setdefault(d["kind"], []).append(d)

    def test_consensus_container_is_teku_image_with_helper_command(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        containers = sts["spec"]["template"]["spec"]["containers"]
        consensus = next(c for c in containers if c["name"] == "consensus")

        self.assertEqual(consensus["image"], TEKU_TEST_IMAGE)
        self.assertEqual(consensus["command"], ["/bin/sh", "-ec"])
        script = consensus["args"][0]
        self.assertIn("exec /opt/teku/bin/teku", script)
        # Teku takes the ephemeral chain config via --network=<file>.
        self.assertIn("--network=/network/files/config.yaml", script)
        # Checkpoint sync is required for Ephemery — no genesis-catchup path.
        # --checkpoint-sync-url takes a Beacon API base URL (Checkpointz);
        # Teku's --initial-state expects a direct SSZ state file/URL and is
        # not a substitute for the base-URL flag.
        self.assertIn(
            "--checkpoint-sync-url=https://checkpoint-sync.example.invalid/",
            script,
        )
        self.assertNotIn("--initial-state=", script)
        self.assertIn("--p2p-discovery-bootnodes=", script)
        self.assertIn("--data-path=/data", script)
        self.assertIn("--ee-endpoint=http://127.0.0.1:8551", script)
        self.assertIn("--ee-jwt-secret-file=/jwt/jwt.hex", script)
        self.assertIn("--rest-api-port=5052", script)
        self.assertIn("--metrics-port=8008", script)
        # Teku defaults its metrics-host-allowlist to localhost only, so a
        # Prometheus scraper hitting the Pod IP is rejected without this
        # wildcard. Port 8008 ingress is already gated at L4 by the chart's
        # NetworkPolicy to the observability namespace only.
        self.assertIn("--metrics-host-allowlist=*", script)
        # No Lighthouse signatures leak in.
        self.assertNotIn("exec lighthouse", script)
        self.assertNotIn("--testnet-dir", script)

    def test_consensus_container_uses_teku_flag_shape_not_lighthouse(self) -> None:
        sts = self.by_kind["StatefulSet"][0]
        consensus = next(
            c for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        script = consensus["args"][0]
        # Lighthouse uses --http and --metrics as bare enable-flags; Teku uses
        # --rest-api-enabled=true and --metrics-enabled=true.
        self.assertIn("--rest-api-enabled=true", script)
        self.assertIn("--metrics-enabled=true", script)

    def test_prometheusrule_unions_teku_consensus_metrics(self) -> None:
        rule = next(
            d for d in self.documents if d["kind"] == "PrometheusRule"
        )
        rules = rule["spec"]["groups"][0]["rules"]
        by_record = {r["record"]: r["expr"] for r in rules}
        # Teku metric names appear inside the consensus-side unions.
        peers_expr = by_record["validator_platform_consensus_peers"]
        self.assertIn("beacon_peer_count", peers_expr)
        self.assertIn('consensus_client="teku"', peers_expr)

    def test_lighthouse_ephemery_still_renders_unchanged(self) -> None:
        # Flip the same profile back to Lighthouse and confirm the Lighthouse
        # command still renders identically to today's chart output.
        values = teku_ephemery_values()
        values["consensusClient"] = "lighthouse"
        # Lighthouse's default image is defined in chart values.yaml; no
        # override needed. Drop the teku consensusClients override so the
        # test doesn't depend on it staying present.
        values.pop("consensusClients")
        documents = helm_template(values)
        sts = next(d for d in documents if d["kind"] == "StatefulSet")
        consensus = next(
            c for c in sts["spec"]["template"]["spec"]["containers"]
            if c["name"] == "consensus"
        )
        script = consensus["args"][0]
        self.assertIn("exec lighthouse bn", script)
        self.assertIn("--testnet-dir=/network/files", script)
        self.assertNotIn("teku", script)


if __name__ == "__main__":
    unittest.main()
