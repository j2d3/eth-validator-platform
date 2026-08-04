"""Contracts for projecting identity-separated EIP-2335 keys into Web3Signer."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "platform" / "apps" / "dev"


def render() -> list[dict]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(APPS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def object_named(documents: list[dict], kind: str, name: str) -> dict:
    return next(
        document
        for document in documents
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
    )


class Web3SignerKeyProjectionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = render()

    def test_external_secret_uses_only_the_signing_reader(self) -> None:
        external = object_named(
            self.documents, "ExternalSecret", "web3signer-validator-keystore"
        )
        self.assertEqual(external["metadata"]["namespace"], "signing")
        self.assertEqual(
            external["spec"]["secretStoreRef"],
            {"kind": "ClusterSecretStore", "name": "aws-signing-secrets"},
        )
        self.assertEqual(
            {
                item["secretKey"]: item["remoteRef"]
                for item in external["spec"]["data"]
            },
            {
                "keystore": {
                    "key": "eth-validator-platform-dev/signing/validator-keystore",
                    "property": "keystore",
                },
                "password": {
                    "key": "eth-validator-platform-dev/signing/validator-keystore",
                    "property": "password",
                },
                "keystore02": {
                    "key": "eth-validator-platform-dev/signing/validator-keystore-02",
                    "property": "keystore",
                },
                "password02": {
                    "key": "eth-validator-platform-dev/signing/validator-keystore-02",
                    "property": "password",
                },
            },
        )

    def test_secret_template_has_one_descriptor_per_validator_identity(self) -> None:
        external = object_named(
            self.documents, "ExternalSecret", "web3signer-validator-keystore"
        )
        template = external["spec"]["target"]["template"]
        self.assertEqual(template["engineVersion"], "v2")
        self.assertEqual(set(template["data"]), {
            "validator.json",
            "validator.password",
            "validator.yaml",
            "validator-02.json",
            "validator-02.password",
            "validator-02.yaml",
        })
        descriptors = {
            name: yaml.safe_load(template["data"][name])
            for name in ("validator.yaml", "validator-02.yaml")
        }
        self.assertEqual(
            descriptors,
            {
                "validator.yaml": {
                    "type": "file-keystore",
                    "keyType": "BLS",
                    "keystoreFile": "/var/run/web3signer/keys/validator.json",
                    "keystorePasswordFile": "/var/run/web3signer/keys/validator.password",
                },
                "validator-02.yaml": {
                    "type": "file-keystore",
                    "keyType": "BLS",
                    "keystoreFile": "/var/run/web3signer/keys/validator-02.json",
                    "keystorePasswordFile": "/var/run/web3signer/keys/validator-02.password",
                },
            },
        )

    def test_web3signer_mounts_only_the_projected_secret_read_only(self) -> None:
        deployment = object_named(self.documents, "Deployment", "web3signer")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        key_volume = next(volume for volume in pod["volumes"] if volume["name"] == "key-store")
        key_mount = next(
            mount for mount in container["volumeMounts"] if mount["name"] == "key-store"
        )

        self.assertEqual(pod["securityContext"]["fsGroup"], 999)
        self.assertEqual(pod["securityContext"]["fsGroupChangePolicy"], "OnRootMismatch")
        self.assertTrue(key_mount["readOnly"])
        self.assertEqual(
            key_volume["secret"],
            {
                "secretName": "web3signer-validator-keystore",
                "defaultMode": 0o440,
                "items": [
                    {"key": "validator.json", "path": "validator.json"},
                    {"key": "validator.password", "path": "validator.password"},
                    {"key": "validator.yaml", "path": "validator.yaml"},
                    {"key": "validator-02.json", "path": "validator-02.json"},
                    {"key": "validator-02.password", "path": "validator-02.password"},
                    {"key": "validator-02.yaml", "path": "validator-02.yaml"},
                ],
            },
        )
        self.assertNotIn("emptyDir", key_volume)

    def test_web3signer_heap_admits_standard_eip2335_scrypt(self) -> None:
        deployment = object_named(self.documents, "Deployment", "web3signer")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        environment = {item["name"]: item.get("value") for item in container["env"]}

        self.assertEqual(environment["JAVA_TOOL_OPTIONS"], "-Xms128m -Xmx640m")
        self.assertEqual(container["resources"]["limits"]["memory"], "1Gi")

    def test_signer_network_matches_qualified_ephemery_assignment(self) -> None:
        deployment = object_named(self.documents, "Deployment", "web3signer")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        args = container["args"]
        profile = object_named(self.documents, "ConfigMap", "platform-profile")

        self.assertIn("--network=/var/run/web3signer/network/config.yaml", args)
        self.assertNotIn("--network=ephemery", args)
        self.assertNotIn("--network=hoodi", args)
        network_mount = next(
            mount for mount in container["volumeMounts"] if mount["name"] == "network-config"
        )
        network_volume = next(
            volume for volume in pod["volumes"] if volume["name"] == "network-config"
        )
        self.assertEqual(
            network_mount,
            {
                "name": "network-config",
                "mountPath": "/var/run/web3signer/network",
                "readOnly": True,
            },
        )
        generated_name = network_volume["configMap"]["name"]
        network_config_map = object_named(self.documents, "ConfigMap", generated_name)
        self.assertEqual(network_config_map["metadata"]["namespace"], "signing")
        config_text = network_config_map["data"]["config.yaml"]
        active_config = "\n".join(
            line for line in config_text.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotRegex(active_config, r"https?://|BOOT_ENR|BOOTNODE")

        network_profile = yaml.safe_load(
            (ROOT / "applications" / "networks" / "ephemery-162.yaml").read_text()
        )
        identity = network_profile["spec"]["identity"]
        bundle_sha = network_profile["spec"]["artifactBundle"]["sha256"]
        self.assertIn(f"# Bundle SHA256: {bundle_sha}", config_text)
        self.assertRegex(
            config_text,
            rf"(?m)^GENESIS_FORK_VERSION: {re.escape(identity['genesisForkVersion'])}$",
        )
        self.assertRegex(
            config_text,
            rf"(?m)^DEPOSIT_CHAIN_ID: {identity['executionChainId']}$",
        )
        self.assertEqual(profile["data"]["signingEnabled"], "true")
        self.assertEqual(profile["data"]["network"], "ephemery-162")
        self.assertNotIn("validator-client", yaml.safe_dump_all(self.documents))


if __name__ == "__main__":
    unittest.main()
