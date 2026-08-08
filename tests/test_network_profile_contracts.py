from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tools import render_local_assignments, validate_catalog


ROOT = Path(__file__).resolve().parents[1]


class NetworkProfileCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = render_local_assignments.load_catalog()
        self.documents = validate_catalog.load_yaml_documents()
        self.validators = validate_catalog.load_validators()

    def test_hoodi_profile_carries_canonical_immutable_identity(self) -> None:
        profile = self.catalog["NetworkProfile"]["hoodi"]["spec"]

        self.assertEqual(profile["family"], "hoodi")
        self.assertEqual(profile["generation"], "permanent")
        self.assertEqual(profile["resetPolicy"], "persistent")
        self.assertEqual(profile["identity"]["executionChainId"], 560048)
        self.assertEqual(profile["identity"]["executionNetworkId"], 560048)
        self.assertEqual(
            profile["identity"]["executionGenesisHash"],
            "0xbbe312868b376a3001692a646dd2d7d1e4406380dfd86b98aa8a34d1557c971b",
        )
        self.assertEqual(
            profile["identity"]["consensusGenesisValidatorsRoot"],
            "0x212f13fc4df078b6cb7db228f1c8307566dcecf900867401a92023d7ba99cb5f",
        )
        self.assertEqual(profile["identity"]["genesisForkVersion"], "0x10000910")
        self.assertEqual(profile["identity"]["consensusGenesisTime"], 1742213400)

    def test_assignment_and_identity_must_reference_the_same_profile(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if document["kind"] == "ValidatorAssignment":
                document["spec"]["networkProfileRef"] = "ephemery-162"

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("does not match ValidatorIdentity" in error for error in errors))

    def test_unknown_network_profile_fails_closed(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if document["kind"] == "ValidatorIdentity":
                document["spec"]["networkProfileRef"] = "unknown-network"

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("unknown networkProfileRef" in error for error in errors))

    def test_reset_profile_must_be_generation_addressed(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if document["kind"] == "NetworkProfile" and document["metadata"]["name"] == "ephemery-162":
                document["spec"]["generation"] = "163"

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("generation-addressed" in error for error in errors))

    def test_mutable_latest_artifact_url_is_rejected(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if document["kind"] == "NetworkProfile" and document["metadata"]["name"] == "ephemery-162":
                document["spec"]["artifactBundle"]["url"] = (
                    "https://github.com/ephemery-testnet/ephemery-genesis/"
                    "releases/latest/testnet-all.tar.gz"
                )

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("not /latest/" in error for error in errors))

    def test_fingerprint_tracks_chain_identity_not_operator_endpoint(self) -> None:
        profile = copy.deepcopy(self.catalog["NetworkProfile"]["hoodi"])
        original = render_local_assignments.network_identity_fingerprint(profile)

        profile["spec"]["checkpointSync"] = {
            "primaryUrl": "https://checkpoint.example.invalid/",
            "verificationUrls": ["https://checkpoint-verify.example.invalid/"],
        }
        self.assertEqual(render_local_assignments.network_identity_fingerprint(profile), original)

        profile["spec"]["identity"]["executionChainId"] += 1
        self.assertNotEqual(render_local_assignments.network_identity_fingerprint(profile), original)

        profile = copy.deepcopy(self.catalog["NetworkProfile"]["ephemery-162"])
        original = render_local_assignments.network_identity_fingerprint(profile)
        profile["spec"]["artifactBundle"]["sha256"] = "a" * 64
        self.assertNotEqual(render_local_assignments.network_identity_fingerprint(profile), original)

    def test_ephemery_profile_is_exact_and_requires_the_pinned_bundle(self) -> None:
        profile = copy.deepcopy(self.catalog["NetworkProfile"]["ephemery-162"])
        path = ROOT / "applications" / "networks" / "ephemery-162.yaml"

        self.assertEqual(
            validate_catalog.schema_errors([(path, profile)], self.validators), []
        )
        self.assertEqual(profile["spec"]["identity"]["executionChainId"], 39438162)
        self.assertEqual(
            profile["spec"]["identity"]["executionGenesisHash"],
            "0x7398e3663283d8dbbf92bfdfee7dedd311079fb131ec1ce2501d5b7043f3d7a9",
        )
        self.assertEqual(
            profile["spec"]["identity"]["consensusGenesisValidatorsRoot"],
            "0xe7ba535e068e129a2e3b17ee6a8f275eee3d1a01126f583ea7b6e867a91c0e5e",
        )
        self.assertEqual(
            profile["spec"]["artifactBundle"]["sha256"],
            "478ca7181212f2d87137c337e854befbed8aacde8bee8f64d6ca7e28967ee2fb",
        )
        self.assertEqual(len(profile["spec"]["artifactBundle"]["files"]), 14)
        self.assertEqual(
            profile["spec"]["artifactBundle"]["files"]["depositContractBlock"],
            "deposit_contract_block.txt",
        )
        self.assertEqual(
            profile["spec"]["artifactBundle"]["files"]["executionChainspec"],
            "chainspec.json",
        )
        self.assertNotIn(
            "depositDeployBlock", profile["spec"]["artifactBundle"]["files"]
        )

        del profile["spec"]["artifactBundle"]
        errors = validate_catalog.schema_errors([(path, profile)], self.validators)
        self.assertTrue(any("artifactBundle" in error for error in errors))

        profile = copy.deepcopy(self.catalog["NetworkProfile"]["ephemery-162"])
        del profile["spec"]["checkpointSync"]
        errors = validate_catalog.schema_errors([(path, profile)], self.validators)
        self.assertTrue(any("checkpointSync" in error for error in errors))

    def test_builtin_client_and_signer_networks_must_agree(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if document["kind"] == "NetworkProfile" and document["metadata"]["name"] == "hoodi":
                document["spec"]["clients"]["lighthouse"]["network"] = "sepolia"

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("adapters disagree" in error for error in errors))

    def test_ephemery_signing_fails_if_signer_binding_loses_qualification(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if (
                document["kind"] == "NetworkProfile"
                and document["metadata"]["name"] == "ephemery-162"
            ):
                document["spec"]["signer"]["web3signer"][
                    "signingQualified"
                ] = False
            if (
                document["kind"] == "ValidatorAssignment"
                and document["metadata"]["name"] == "assignment-ephemery-162-synthetic"
            ):
                document["spec"]["lifecycle"] = "active"
                document["spec"]["signingEnabled"] = True
                document["spec"]["safety"] = {
                    "slashingProtectionConfirmed": True,
                    "doppelgangerProtectionConfirmed": True,
                }

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("signing is not qualified" in error for error in errors))

    def test_artifact_profile_catalog_requires_reset_and_checkpoint(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if document["kind"] == "NetworkProfile" and document["metadata"]["name"] == "ephemery-162":
                document["spec"]["resetPolicy"] = "persistent"
                del document["spec"]["checkpointSync"]

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("require resetPolicy" in error for error in errors))
        self.assertTrue(any("require checkpointSync" in error for error in errors))


class NetworkProfileRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = render_local_assignments.load_catalog()

    def test_hoodi_projection_contains_typed_profile_not_friendly_string(self) -> None:
        release = render_local_assignments.build_release(
            "assignment-synthetic-01", self.catalog
        )
        values = release["spec"]["values"]

        self.assertNotIn("network", values)
        self.assertEqual(values["networkProfile"]["name"], "hoodi")
        self.assertEqual(values["networkProfile"]["generation"], "permanent")
        self.assertEqual(values["networkProfile"]["clients"]["geth"], {
            "mode": "builtin",
            "network": "hoodi",
        })
        self.assertRegex(values["networkProfile"]["identityFingerprint"], r"^[0-9a-f]{64}$")

    def test_ephemery_projection_is_digest_pinned_and_signing_qualified(self) -> None:
        catalog = copy.deepcopy(self.catalog)
        assignment = catalog["ValidatorAssignment"]["assignment-ephemery-162-synthetic"]["spec"]
        assignment["lifecycle"] = "active"
        assignment["signingEnabled"] = True
        assignment["safety"] = {
            "slashingProtectionConfirmed": True,
            "doppelgangerProtectionConfirmed": True,
        }
        release = render_local_assignments.build_release(
            "assignment-ephemery-162-synthetic", catalog
        )
        values = release["spec"]["values"]
        network = values["networkProfile"]

        self.assertEqual(network["name"], "ephemery-162")
        self.assertEqual(network["resetPolicy"], "replace-data")
        self.assertEqual(network["clients"]["geth"]["mode"], "artifact-bundle")
        self.assertIsNone(network["clients"]["geth"]["network"])
        self.assertEqual(
            network["artifactBundle"]["sha256"],
            "478ca7181212f2d87137c337e854befbed8aacde8bee8f64d6ca7e28967ee2fb",
        )
        self.assertTrue(network["signer"]["web3signer"]["signingQualified"])
        self.assertIsNone(network["signer"]["web3signer"]["network"])
        self.assertTrue(values["validator"]["enabled"])
        self.assertTrue(values["validator"]["slashingProtectionConfirmed"])
        self.assertEqual(
            values["validator"]["publicKey"],
            "0x8b8fb7b0abfa650d1b393e87328b3af24f0605adc807942d1e1b10095f72b06a6fc281cb31d67a32dc0ef1ce9388c47e",
        )
        self.assertNotIn("signingSecretRef", yaml.safe_dump(release))

        catalog = copy.deepcopy(self.catalog)
        del catalog["NetworkProfile"]["ephemery-162"]["spec"]["checkpointSync"]
        with self.assertRaisesRegex(
            render_local_assignments.ProjectionError, "requires checkpointSync"
        ):
            render_local_assignments.build_release(
                "assignment-ephemery-162-synthetic", catalog
            )

    def test_reset_pvc_names_preserve_long_pair_reference_entropy(self) -> None:
        catalog = copy.deepcopy(self.catalog)
        assignment = catalog["ValidatorAssignment"]["assignment-ephemery-162-synthetic"]["spec"]
        assignment["lifecycle"] = "active"
        assignment["signingEnabled"] = True
        assignment["safety"] = {
            "slashingProtectionConfirmed": True,
            "doppelgangerProtectionConfirmed": True,
        }
        release = render_local_assignments.build_release(
            "assignment-ephemery-162-synthetic", catalog
        )

        def render_pvc_names(fullname: str) -> set[str]:
            values = copy.deepcopy(release["spec"]["values"])
            values["fullnameOverride"] = fullname
            with tempfile.TemporaryDirectory() as directory:
                values_path = Path(directory) / "values.yaml"
                values_path.write_text(yaml.safe_dump(values), encoding="utf-8")
                result = subprocess.run(
                    [
                        "helm",
                        "template",
                        "pair-test",
                        str(ROOT / "charts" / "ethereum-node"),
                        "--namespace",
                        "ethereum",
                        "--values",
                        str(values_path),
                        "--set",
                        "lifecycleState=active",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            return {
                document["metadata"]["name"]
                for document in yaml.safe_load_all(result.stdout)
                if document and document.get("kind") == "PersistentVolumeClaim"
            }

        first = render_pvc_names("pair-" + "x" * 40 + "-one")
        second = render_pvc_names("pair-" + "x" * 40 + "-two")
        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 3)
        self.assertTrue(first.isdisjoint(second))

    def test_mixed_network_adapter_modes_fail_projection(self) -> None:
        catalog = copy.deepcopy(self.catalog)
        profile = catalog["NetworkProfile"]["ephemery-162"]
        profile["spec"]["clients"]["geth"] = {"mode": "builtin", "network": "hoodi"}

        with self.assertRaisesRegex(render_local_assignments.ProjectionError, "modes must match"):
            render_local_assignments.build_release(
                "assignment-ephemery-162-synthetic", catalog
            )

    def test_client_arguments_are_internal_adapters_not_values_arrays(self) -> None:
        values = (ROOT / "charts" / "ethereum-node" / "values.yaml").read_text(
            encoding="utf-8"
        )
        node = (ROOT / "charts" / "ethereum-node" / "templates" / "node.yaml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("args:", values.split("executionClients:", 1)[1])
        self.assertNotIn("$execution.args", node)
        self.assertNotIn("$consensus.args", node)
        # The chart's mode-detection variables were renamed from $gethAdapter
        # to $executionAdapter and from $lighthouseAdapter to $consensusAdapter
        # so second EL/CL adapters (e.g. Reth, Teku) read their own network
        # entries rather than always Geth's or Lighthouse's. The intent — args
        # come from the internal per-client network adapter, not a raw values
        # array — is preserved.
        self.assertIn("$executionAdapter.network", node)
        self.assertIn("$consensusAdapter.network", node)
        self.assertIn("command: [lighthouse]", node)
        validator = (
            ROOT / "charts" / "ethereum-node" / "templates" / "validator-client.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("command: [lighthouse]", validator)
        self.assertIn("sha256sum -c", node)
        # The artifact-mode Lighthouse command was extracted into
        # `ethereum-node.lighthouseRunCommand` (in _helpers.tpl) so a second
        # CL adapter (Teku, Nimbus) can plug in through the same dispatcher
        # without touching node.yaml. The `--testnet-dir=/network/files` flag
        # now lives inside the helper, not the template body.
        helpers = (
            ROOT / "charts" / "ethereum-node" / "templates" / "_helpers.tpl"
        ).read_text(encoding="utf-8")
        self.assertIn("--testnet-dir=/network/files", helpers)
        self.assertNotIn(".Values.clientArgs", node)

    def test_current_shared_signer_matches_profile_but_is_single_network(self) -> None:
        profile = self.catalog["NetworkProfile"]["hoodi"]["spec"]
        signer_network = profile["signer"]["web3signer"]["network"]
        deployment = (
            ROOT / "platform" / "apps" / "base" / "web3signer" / "deployment.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn(f"--network={signer_network}", deployment)
        self.assertEqual(deployment.count("--network="), 1)
        self.assertNotIn("ephemery", deployment.lower())

    def test_committed_defaults_share_the_catalog_fingerprint(self) -> None:
        expected = render_local_assignments.network_identity_fingerprint(
            self.catalog["NetworkProfile"]["hoodi"]
        )
        with (ROOT / "charts" / "ethereum-node" / "values.yaml").open(
            encoding="utf-8"
        ) as stream:
            chart_values = yaml.safe_load(stream)
        with (ROOT / "platform" / "apps" / "local" / "profile.yaml").open(
            encoding="utf-8"
        ) as stream:
            local_profile = yaml.safe_load(stream)

        self.assertEqual(chart_values["networkProfile"]["identityFingerprint"], expected)
        self.assertEqual(local_profile["data"]["networkIdentity"], expected)


if __name__ == "__main__":
    unittest.main()
