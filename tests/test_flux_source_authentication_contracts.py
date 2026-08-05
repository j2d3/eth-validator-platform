"""Static contracts for the private Flux source evaluation in issue #71."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "hack" / "qualification" / "flux-source-authentication.yaml"
ADR_PATH = ROOT / "docs" / "adrs" / "0002-private-flux-source-authentication.md"
RUNBOOK_PATH = ROOT / "docs" / "runbooks" / "flux-source-authentication.md"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


class FluxSourceAuthenticationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_yaml(CONTRACT_PATH)

    def mutated(self) -> dict:
        return copy.deepcopy(self.contract)

    def test_slice_is_design_only_and_records_no_mutation(self) -> None:
        status = self.contract["status"]
        self.assertIs(status["design_only"], True)
        for field in (
            "github_app_created",
            "aws_resources_created",
            "credentials_issued",
            "workflow_authority_changed",
            "cluster_changed",
            "failure_drill_executed",
        ):
            self.assertIs(status[field], False)

    def test_current_source_matches_checked_in_ssh_deploy_key_manifest(self) -> None:
        current = self.contract["current_source"]
        documents = list(yaml.safe_load_all((ROOT / current["configured"]["source_manifest"]).read_text()))
        source = next(doc for doc in documents if doc and doc.get("kind") == "GitRepository")

        self.assertEqual(current["repository_visibility"], "public")
        self.assertIs(current["anonymous_https"]["available"], True)
        self.assertIs(current["anonymous_https"]["configured"], False)
        self.assertEqual(source["spec"]["url"], current["configured"]["url"])
        self.assertEqual(source["spec"]["secretRef"]["name"], current["configured"]["secret_ref"])
        self.assertTrue(source["spec"]["url"].startswith("ssh://"))

    def test_github_app_source_uses_provider_https_and_exact_secret_shape(self) -> None:
        option = self.contract["private_source_options"]["direct_github_app"]
        source = option["source"]
        secret = option["secret_contract"]

        self.assertEqual(source["kind"], "GitRepository")
        self.assertEqual(source["provider"], "github")
        self.assertTrue(source["url"].startswith("https://"))
        self.assertEqual(
            set(secret["required_keys"]),
            {"githubAppID", "githubAppPrivateKey"},
        )
        self.assertEqual(
            set(secret["exactly_one_of"]),
            {"githubAppInstallationOwner", "githubAppInstallationID"},
        )

    def test_github_app_design_does_not_call_it_credential_free(self) -> None:
        credentials = self.contract["private_source_options"]["direct_github_app"]["credentials"]
        self.assertIs(credentials["app_private_key"]["long_lived"], True)
        self.assertIs(credentials["app_private_key"]["expires"], False)
        self.assertEqual(credentials["installation_token"]["ttl_seconds"], 3600)

    def test_github_app_api_checks_use_app_and_installation_credentials(self) -> None:
        api = self.contract["private_source_options"]["direct_github_app"]["api_verification"]
        self.assertEqual(api["list_app_installations_auth"], "github-app-jwt")
        self.assertEqual(api["create_installation_token_auth"], "github-app-jwt")
        self.assertEqual(api["list_installation_repositories_auth"], "installation-access-token")
        self.assertIs(api["ordinary_gh_user_token_sufficient"], False)

    def test_github_app_bootstrap_does_not_depend_on_flux_installed_eso(self) -> None:
        bootstrap = self.contract["private_source_options"]["direct_github_app"]["bootstrap"]
        self.assertEqual(bootstrap["owner"], "trusted-local-operator")
        self.assertIs(bootstrap["depends_on_eso"], False)

    def test_oci_promotion_packages_repository_root_not_cluster_directory(self) -> None:
        promotion = self.contract["private_source_options"]["signed_oci_promotion"]["promotion"]
        self.assertEqual(promotion["source_root"], ".")
        self.assertEqual(promotion["forbidden_source_root"], "clusters/dev")

    def test_oci_artifact_contains_every_direct_dev_source_path(self) -> None:
        promotion = self.contract["private_source_options"]["signed_oci_promotion"]["promotion"]
        roots = {Path(path) for path in promotion["required_reference_roots"]}
        declared_paths: set[Path] = set()

        for manifest in sorted((ROOT / "clusters" / "dev").glob("*.yaml")):
            for document in yaml.safe_load_all(manifest.read_text()):
                if not document or document.get("kind") != "Kustomization":
                    continue
                spec = document.get("spec", {})
                if spec.get("sourceRef", {}).get("kind") == "GitRepository":
                    declared_paths.add(Path(spec["path"].removeprefix("./")))

        self.assertTrue(declared_paths)
        self.assertTrue(declared_paths.issubset(roots))
        self.assertIn(Path("charts/ethereum-node"), roots)
        self.assertIn(Path("platform/apps/local/assignments"), roots)
        for path in roots:
            self.assertTrue((ROOT / path).exists(), f"missing promoted reference root: {path}")

    def test_keyless_verifier_records_rekor_not_fulcio_as_runtime_endpoint(self) -> None:
        keyless = self.contract["private_source_options"]["signed_oci_promotion"]["keyless_verification"]
        self.assertEqual(keyless["runtime_network_dependencies"], ["rekor.sigstore.dev"])
        self.assertIs(keyless["runtime_fulcio_issuance_required_by_source_controller"], False)

    def test_oci_source_has_an_update_channel_and_digest_bound_verification(self) -> None:
        artifact = self.contract["private_source_options"]["signed_oci_promotion"]["artifact"]
        reference = artifact["reference"]
        self.assertEqual(reference["strategy"], "mutable-promotion-channel")
        self.assertEqual(reference["tag"], "main")
        self.assertEqual(
            reference["resolved_identity"],
            "status-artifact-digest-and-revision",
        )
        self.assertEqual(artifact["verification_provider"], "cosign")

    def test_failure_contract_does_not_overclaim_stale_artifact_behavior(self) -> None:
        failure = self.contract["failure_behavior"]
        self.assertIs(failure["new_failed_or_unverified_revision_published_as_source_artifact"], False)
        self.assertIs(failure["last_successful_artifact_may_remain_available"], True)
        self.assertIs(failure["existing_workloads_may_continue_running"], True)
        self.assertEqual(failure["exact_downstream_readiness_and_drift_reconciliation"], "unqualified")
        self.assertIs(failure["requires_failure_drill_before_stronger_claim"], True)

    def test_docs_exist_and_link_to_contract(self) -> None:
        self.assertTrue(ADR_PATH.is_file())
        self.assertTrue(RUNBOOK_PATH.is_file())
        adr = ADR_PATH.read_text()
        runbook = RUNBOOK_PATH.read_text()
        self.assertIn("flux-source-authentication.yaml", adr)
        self.assertIn("ADR 0002", runbook)
        self.assertNotIn("--path=./clusters/dev", runbook)
        self.assertIn("--path=.", runbook)


if __name__ == "__main__":
    unittest.main()
