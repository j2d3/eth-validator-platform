"""Fail-closed contracts for the declared Flux-on-EKS bootstrap slice.

These tests prove repository structure and rendered-input intent. They do not
claim that Flux, External Secrets, RDS, or an Ethereum client has run on EKS.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLUSTER = ROOT / "clusters" / "dev"
CONTROLLERS = ROOT / "platform" / "infrastructure" / "overlays" / "dev" / "controllers"
CONFIGS = ROOT / "platform" / "infrastructure" / "configs" / "dev"
SIGNER_CONFIGS = CONFIGS / "signer"
PREREQUISITES = ROOT / "platform" / "apps" / "prerequisites" / "dev"
APPS = ROOT / "platform" / "apps" / "dev"
NODE_APPS = ROOT / "platform" / "apps" / "nodes" / "dev"
PORTAL_APPS = ROOT / "platform" / "apps" / "portal" / "dev"
WORKFLOWS = ROOT / ".github" / "workflows"
RUNBOOK = ROOT / "docs" / "runbooks" / "eks-flux-bootstrap.md"
NETWORK_POLICY_PROBE = ROOT / "hack" / "qualification" / "eks-network-policy-probe.yaml"


def load_one(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise AssertionError(f"{path} did not contain exactly one YAML object")
    return document


def load_all(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [document for document in yaml.safe_load_all(stream) if document]


def render_all(path: Path) -> list[dict]:
    """Render a Kustomize layer exactly as CI does, then parse every object."""

    result = subprocess.run(
        ["kubectl", "kustomize", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def object_named(documents: list[dict], kind: str, name: str) -> dict:
    for document in documents:
        if document.get("kind") == kind and document.get("metadata", {}).get("name") == name:
            return document
    raise AssertionError(f"rendered {kind}/{name} not found")


def assert_rds_and_dns_egress(test: unittest.TestCase, policy: dict) -> None:
    egress = policy["spec"]["egress"]
    rds_rules = [
        rule
        for rule in egress
        if any(
            item.get("ipBlock", {}).get("cidr") == "10.42.0.0/16"
            for item in rule.get("to", [])
        )
    ]
    test.assertEqual(len(rds_rules), 1)
    test.assertEqual(rds_rules[0]["ports"], [{"port": 5432, "protocol": "TCP"}])

    dns_rules = [
        rule
        for rule in egress
        if any(
            item.get("namespaceSelector", {}).get("matchLabels", {}).get(
                "kubernetes.io/metadata.name"
            )
            == "kube-system"
            for item in rule.get("to", [])
        )
    ]
    test.assertEqual(len(dns_rules), 1)
    test.assertEqual(
        {(port["port"], port["protocol"]) for port in dns_rules[0]["ports"]},
        {(53, "TCP"), (53, "UDP")},
    )


class EksFluxEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layers = {
            name: load_one(CLUSTER / f"{name}.yaml")
            for name in (
                "infrastructure-controllers",
                "infrastructure-configs",
                "portal-observability",
                "node-apps",
                "signer-infrastructure-configs",
                "signer-prerequisites",
                "apps",
            )
        }

    def test_dependency_chain_is_ordered_and_fail_closed(self) -> None:
        dependencies = {
            name: [item["name"] for item in layer["spec"].get("dependsOn", [])]
            for name, layer in self.layers.items()
        }
        self.assertEqual(dependencies["infrastructure-controllers"], [])
        self.assertEqual(dependencies["infrastructure-configs"], ["infrastructure-controllers"])
        self.assertEqual(dependencies["portal-observability"], ["infrastructure-configs"])
        self.assertEqual(
            dependencies["node-apps"],
            ["infrastructure-controllers", "apps"],
        )
        self.assertEqual(
            dependencies["signer-infrastructure-configs"],
            ["infrastructure-configs"],
        )
        self.assertEqual(
            dependencies["signer-prerequisites"],
            ["signer-infrastructure-configs"],
        )
        self.assertEqual(dependencies["apps"], ["signer-prerequisites"])

        self.assertNotIn("suspend", self.layers["infrastructure-controllers"]["spec"])
        self.assertNotIn("suspend", self.layers["infrastructure-configs"]["spec"])
        self.assertNotIn("suspend", self.layers["portal-observability"]["spec"])
        # node-apps has been reviewed-unsuspended to admit the stopped
        # HelmRelease per docs/runbooks/eks-ephemery-sync.md §4. The
        # HelmRelease itself remains lifecycleState=stopped and non-signing;
        # that safety property is asserted separately in
        # test_eks_ephemery_sync_contracts.
        self.assertIn("suspend", self.layers["node-apps"]["spec"])
        self.assertFalse(self.layers["node-apps"]["spec"]["suspend"])
        # The signer adapter, prerequisite layer, and empty-key workload have
        # been separately reviewed after the RDS credential bootstrap, TLS,
        # migration, and branch-ENI paths were qualified. Validator duties are
        # gated separately from this shared signer release.
        self.assertFalse(self.layers["signer-infrastructure-configs"]["spec"]["suspend"])
        self.assertFalse(self.layers["signer-prerequisites"]["spec"]["suspend"])
        self.assertFalse(self.layers["apps"]["spec"]["suspend"])

    def test_every_layer_is_dev_labeled_and_waits_for_health(self) -> None:
        for name, layer in self.layers.items():
            with self.subTest(layer=name):
                self.assertEqual(
                    layer["metadata"]["labels"]["platform.galaxy-lab/environment"],
                    "dev",
                )
                self.assertEqual(
                    layer["spec"]["commonMetadata"]["labels"][
                        "platform.galaxy-lab/environment"
                    ],
                    "dev",
                )
                self.assertTrue(layer["spec"]["prune"])
                self.assertTrue(layer["spec"]["wait"])

    def test_config_layer_requires_the_scoped_role_input_configmap(self) -> None:
        for name in ("infrastructure-configs", "signer-infrastructure-configs"):
            with self.subTest(layer=name):
                post_build = self.layers[name]["spec"]["postBuild"]
                self.assertEqual(
                    post_build["substituteFrom"],
                    [{"kind": "ConfigMap", "name": "aws-secret-store-role-arns", "optional": False}],
                )
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("external_secrets_reader_role_arns", runbook)
        self.assertIn("EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN", runbook)
        self.assertIn("EXTERNAL_SECRETS_DATABASE_READER_ROLE_ARN", runbook)
        self.assertIn("EXTERNAL_SECRETS_SIGNING_READER_ROLE_ARN", runbook)
        self.assertIn("WEB3SIGNER_POD_SECURITY_GROUP_ID", runbook)
        self.assertIn("WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID", runbook)
        self.assertIn("prune=disabled", runbook)
        self.assertLess(
            runbook.index(
                "kubectl apply -f clusters/local/flux-system/gotk-components.yaml"
            ),
            runbook.index(
                "kubectl -n flux-system create configmap aws-secret-store-role-arns"
            ),
        )

    def test_controller_layer_requires_the_exact_ingress_certificate_input(self) -> None:
        post_build = self.layers["infrastructure-controllers"]["spec"]["postBuild"]
        self.assertEqual(
            post_build["substituteFrom"],
            [{"kind": "ConfigMap", "name": "aws-ingress-inputs", "optional": False}],
        )

    def test_common_config_needs_only_engine_input_and_signer_branch_fails_closed(self) -> None:
        common = yaml.safe_dump_all(render_all(CONFIGS))
        signer = yaml.safe_dump_all(render_all(SIGNER_CONFIGS))

        self.assertIn("${EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN}", common)
        for signer_only in (
            "${EXTERNAL_SECRETS_DATABASE_READER_ROLE_ARN}",
            "${EXTERNAL_SECRETS_SIGNING_READER_ROLE_ARN}",
            "${WEB3SIGNER_POD_SECURITY_GROUP_ID}",
            "${WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID}",
        ):
            with self.subTest(signer_only=signer_only):
                self.assertNotIn(signer_only, common)
                self.assertIn(signer_only, signer)
        self.assertNotIn("${EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN}", signer)

    def test_rendered_signer_config_declares_exact_runtime_and_migration_groups(self) -> None:
        documents = render_all(SIGNER_CONFIGS)
        runtime = object_named(documents, "SecurityGroupPolicy", "web3signer")
        migration = object_named(documents, "SecurityGroupPolicy", "web3signer-schema")

        self.assertEqual(runtime["apiVersion"], "vpcresources.k8s.aws/v1beta1")
        self.assertEqual(runtime["metadata"]["namespace"], "signing")
        self.assertEqual(
            runtime["spec"]["podSelector"]["matchLabels"],
            {"app.kubernetes.io/name": "web3signer"},
        )
        self.assertEqual(
            runtime["spec"]["securityGroups"]["groupIds"],
            ["${WEB3SIGNER_POD_SECURITY_GROUP_ID}"],
        )
        self.assertEqual(migration["metadata"]["namespace"], "database")
        self.assertEqual(
            migration["spec"]["podSelector"]["matchLabels"],
            {
                "app.kubernetes.io/name": "web3signer-schema",
                "app.kubernetes.io/component": "database-migration",
            },
        )
        self.assertEqual(
            migration["spec"]["securityGroups"]["groupIds"],
            ["${WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID}"],
        )
        migration_job = object_named(
            render_all(PREREQUISITES),
            "Job",
            "web3signer-schema-v12",
        )
        job_labels = migration_job["spec"]["template"]["metadata"]["labels"]
        self.assertEqual(
            migration["spec"]["podSelector"]["matchLabels"],
            {
                key: job_labels[key]
                for key in (
                    "app.kubernetes.io/name",
                    "app.kubernetes.io/component",
                )
            },
        )

    def test_application_namespaces_start_with_default_deny(self) -> None:
        policies = load_all(CONFIGS / "default-deny.yaml")
        self.assertEqual(
            {policy["metadata"]["namespace"] for policy in policies},
            {"database", "signing", "portal-system", "ethereum"},
        )
        for policy in policies:
            self.assertEqual(policy["spec"]["podSelector"], {})
            self.assertEqual(set(policy["spec"]["policyTypes"]), {"Ingress", "Egress"})

    def test_paths_name_only_eks_overlays(self) -> None:
        expected = {
            "infrastructure-controllers": "./platform/infrastructure/overlays/dev/controllers",
            "infrastructure-configs": "./platform/infrastructure/configs/dev",
            "portal-observability": "./platform/apps/portal/dev",
            "node-apps": "./platform/apps/nodes/dev",
            "signer-infrastructure-configs": "./platform/infrastructure/configs/dev/signer",
            "signer-prerequisites": "./platform/apps/prerequisites/dev",
            "apps": "./platform/apps/dev",
        }
        self.assertEqual(
            {name: layer["spec"]["path"] for name, layer in self.layers.items()},
            expected,
        )

    def test_pinned_flux_overlay_is_inside_the_root_inventory(self) -> None:
        root = load_one(CLUSTER / "kustomization.yaml")
        self.assertIn("flux-system", root["resources"])
        overlay = load_one(CLUSTER / "flux-system" / "kustomization.yaml")
        self.assertEqual(overlay["resources"], ["../../local/flux-system"])
        sync_patch = overlay["patches"][0]
        self.assertEqual(sync_patch["target"]["name"], "flux-system")
        self.assertIn("value: ./clusters/dev", sync_patch["patch"])
        strict_patch = overlay["patches"][1]
        self.assertEqual(strict_patch["target"]["name"], "kustomize-controller")
        self.assertIn(
            "--feature-gates=StrictPostBuildSubstitutions=true",
            strict_patch["patch"],
        )

        sync_documents = load_all(
            ROOT / "clusters" / "local" / "flux-system" / "gotk-sync.yaml"
        )
        source = next(document for document in sync_documents if document["kind"] == "GitRepository")
        self.assertEqual(source["spec"]["url"], "ssh://git@github.com/j2d3/eth-validator-platform")
        self.assertEqual(source["spec"]["secretRef"], {"name": "flux-system"})
        self.assertNotIn("password", yaml.safe_dump(source).lower())


class EksAwsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        stores = load_all(CONFIGS / "aws-secret-stores.yaml") + load_all(
            SIGNER_CONFIGS / "aws-signer-secret-stores.yaml"
        )
        self.stores = {store["metadata"]["name"]: store for store in stores}

    def test_secret_stores_use_ambient_pod_identity_without_static_credentials(self) -> None:
        self.assertEqual(
            set(self.stores),
            {"aws-engine-secrets", "aws-database-secrets", "aws-signing-secrets"},
        )
        for name, store in self.stores.items():
            with self.subTest(store=name):
                provider = store["spec"]["provider"]["aws"]
                expected_role = {
                    "aws-engine-secrets": "${EXTERNAL_SECRETS_ENGINE_READER_ROLE_ARN}",
                    "aws-database-secrets": "${EXTERNAL_SECRETS_DATABASE_READER_ROLE_ARN}",
                    "aws-signing-secrets": "${EXTERNAL_SECRETS_SIGNING_READER_ROLE_ARN}",
                }[name]
                self.assertEqual(
                    provider,
                    {
                        "service": "SecretsManager",
                        "region": "us-west-2",
                        "role": expected_role,
                    },
                )
                self.assertEqual(
                    store["metadata"]["labels"]["platform.galaxy-lab/aws-auth"],
                    "eks-pod-identity",
                )
                self.assertNotIn("auth", provider)
                text = yaml.safe_dump(store).lower()
                for forbidden in ("accesskey", "secretkey", "rolearn", "webidentitytoken"):
                    self.assertNotIn(forbidden, text)
                self.assertNotIn("external_secrets_role_arn", text)
                self.assertNotIn("sts:assumerole", text)

    def test_secret_store_namespace_boundaries_are_explicit(self) -> None:
        engine = self.stores["aws-engine-secrets"]["spec"]["conditions"]
        database = self.stores["aws-database-secrets"]["spec"]["conditions"]
        signing = self.stores["aws-signing-secrets"]["spec"]["conditions"]
        self.assertEqual(engine, [{"namespaces": ["ethereum"]}])
        self.assertEqual(database, [{"namespaces": ["database", "signing"]}])
        self.assertEqual(signing, [{"namespaces": ["signing"]}])

    def test_external_secrets_service_account_matches_terraform_pod_identity(self) -> None:
        terraform = (ROOT / "terraform" / "environments" / "dev" / "main.tf").read_text(
            encoding="utf-8"
        )
        self.assertIn('namespace       = "external-secrets"', terraform)
        self.assertIn('service_account = "external-secrets"', terraform)

        controller_base = load_one(
            ROOT / "platform" / "infrastructure" / "controllers" / "external-secrets.yaml"
        )
        self.assertEqual(controller_base["metadata"]["name"], "external-secrets")
        self.assertEqual(controller_base["metadata"]["namespace"], "external-secrets")

    def test_eks_controller_overlay_excludes_local_database_and_logging_controllers(self) -> None:
        overlay = load_one(CONTROLLERS / "kustomization.yaml")
        deleted = {
            patch["target"]["name"]
            for patch in overlay["patches"]
            if "$patch: delete" in patch.get("patch", "")
        }
        self.assertEqual(
            deleted,
            {"cnpg-system", "cloudnative-pg", "grafana", "loki", "alloy"},
        )
        monitoring_patch = load_one(CONTROLLERS / "monitoring-patch.yaml")
        self.assertEqual(
            monitoring_patch["spec"]["values"]["grafana"]["additionalDataSources"],
            [],
        )

    def test_existing_storage_class_is_registered_once_not_reimplemented(self) -> None:
        kustomization = load_one(CONFIGS / "kustomization.yaml")
        self.assertEqual(kustomization["resources"].count("ebs-gp3-storage-class.yaml"), 1)

        # Inspect the repository inventory, not every file physically nested
        # below the checkout. Claude and other tools may keep isolated Git
        # worktrees under an ignored .claude/worktrees directory; a filesystem
        # rglob would count those independent checkouts as duplicate desired
        # state even though Git contains exactly one definition.
        tracked = subprocess.run(
            ["git", "ls-files", "--", ":(glob)**/ebs-gp3-storage-class.yaml"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertEqual(
            tracked,
            ["platform/infrastructure/configs/dev/ebs-gp3-storage-class.yaml"],
        )

    def test_database_secret_is_reference_only_and_tls_is_verify_full(self) -> None:
        external_secret = load_one(PREREQUISITES / "database-secret.yaml")
        keys = {item["remoteRef"]["key"] for item in external_secret["spec"]["data"]}
        properties = {item["remoteRef"]["property"] for item in external_secret["spec"]["data"]}
        self.assertEqual(keys, {"eth-validator-platform-dev/signing/web3signer-database"})
        self.assertEqual(properties, {"host", "port", "database", "username", "password"})
        self.assertNotIn("dataFrom", external_secret["spec"])

        contract_text = yaml.safe_dump(external_secret)
        self.assertIn("signing/web3signer-database", contract_text)
        self.assertIn("property: database", contract_text)
        self.assertIn("secretKey: dbname", contract_text)

        rendered_inputs = (
            (PREREQUISITES / "kustomization.yaml").read_text(encoding="utf-8")
            + (APPS / "kustomization.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(rendered_inputs.count("sslmode=verify-full"), 2)
        self.assertNotIn("sslmode=disable", rendered_inputs)

    def test_rds_egress_matches_the_default_terraform_vpc_and_is_port_bounded(self) -> None:
        variables = (ROOT / "terraform" / "environments" / "dev" / "variables.tf").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r'variable "vpc_cidr".*?default\s*=\s*"([^"]+)"',
            variables,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        vpc_cidr = match.group(1)

        for path in (PREREQUISITES / "kustomization.yaml", APPS / "kustomization.yaml"):
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn(f"cidr: {vpc_cidr}", text)
                self.assertIn("port: 5432", text)
                self.assertNotIn("0.0.0.0/0", text)


class EksApplicationSafetyTests(unittest.TestCase):
    def test_network_policy_probe_is_a_same_path_managed_pod_experiment(self) -> None:
        documents = load_all(NETWORK_POLICY_PROBE)
        deployments = {
            document["metadata"]["name"]: document
            for document in documents
            if document["kind"] == "Deployment"
        }
        self.assertEqual(
            set(deployments),
            {
                "network-policy-probe-server",
                "network-policy-probe-allowed",
                "network-policy-probe-denied",
            },
        )
        self.assertNotIn("SecurityGroupPolicy", {document["kind"] for document in documents})

        allowed_labels = deployments["network-policy-probe-allowed"]["spec"]["template"][
            "metadata"
        ]["labels"]
        denied_labels = deployments["network-policy-probe-denied"]["spec"]["template"][
            "metadata"
        ]["labels"]
        self.assertEqual(
            allowed_labels["platform.galaxy-lab/network-policy-probe-access"],
            "allowed",
        )
        self.assertEqual(
            denied_labels["platform.galaxy-lab/network-policy-probe-access"],
            "denied",
        )
        self.assertEqual(
            allowed_labels["app.kubernetes.io/name"],
            denied_labels["app.kubernetes.io/name"],
        )

        policy = object_named(
            documents,
            "NetworkPolicy",
            "allow-only-labeled-probe-client",
        )
        ingress = policy["spec"]["ingress"]
        self.assertEqual(len(ingress), 1)
        self.assertEqual(
            ingress[0]["from"],
            [
                {
                    "podSelector": {
                        "matchLabels": {
                            "platform.galaxy-lab/network-policy-probe-access": "allowed"
                        }
                    }
                }
            ],
        )
        self.assertEqual(ingress[0]["ports"], [{"port": 8080, "protocol": "TCP"}])

        for name, deployment in deployments.items():
            with self.subTest(deployment=name):
                pod = deployment["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
                container = pod["containers"][0]
                self.assertIn("@sha256:", container["image"])
                self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
                self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
                self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])

    def test_stopped_catalog_assignment_remains_disabled_in_the_local_overlay(self) -> None:
        generated = load_one(
            ROOT
            / "platform"
            / "apps"
            / "local"
            / "assignments"
            / "assignment-ephemery-162-synthetic.yaml"
        )
        self.assertEqual(generated["spec"]["values"]["lifecycleState"], "stopped")
        self.assertFalse(generated["spec"]["values"]["validator"]["enabled"])

        local_documents = render_all(ROOT / "platform" / "apps" / "local")
        release = object_named(
            local_documents,
            "HelmRelease",
            "assignment-ephemery-162-synthetic",
        )
        values = release["spec"]["values"]
        self.assertFalse(values["validator"]["enabled"])
        self.assertFalse(values["validator"]["slashingProtectionConfirmed"])

        overlay = (NODE_APPS / "kustomization.yaml").read_text(encoding="utf-8")
        self.assertNotIn("lifecycleState", overlay)
        self.assertIn("values-eks-ephemery.yaml", overlay)
        self.assertIn("eth-validator-platform-dev", overlay)

    def test_node_profile_records_the_paused_signing_state(self) -> None:
        profile = load_one(NODE_APPS / "profile.yaml")
        self.assertEqual(profile["data"]["environment"], "dev")
        self.assertEqual(profile["data"]["signingEnabled"], "false")
        self.assertEqual(
            profile["metadata"]["labels"]["platform.galaxy-lab/signing-enabled"],
            "false",
        )

    def test_released_signer_projects_only_the_encrypted_key_secret(self) -> None:
        documents = render_all(APPS)
        deployment = object_named(documents, "Deployment", "web3signer")
        pod = deployment["spec"]["template"]["spec"]
        args = pod["containers"][0]["args"]
        key_store = next(
            volume for volume in pod["volumes"] if volume["name"] == "key-store"
        )

        self.assertEqual(
            key_store["secret"]["secretName"], "web3signer-validator-keystore"
        )
        self.assertNotIn("emptyDir", key_store)
        self.assertIn("--metrics-host-allowlist=*", args)
        self.assertEqual(
            next(arg for arg in args if arg.startswith("--http-host-allowlist=")),
            "--http-host-allowlist=web3signer,web3signer.signing.svc,web3signer.signing.svc.cluster.local",
        )
        self.assertEqual(
            load_one(APPS / "profile.yaml")["data"]["signingEnabled"],
            "true",
        )

    def test_eks_surfaces_are_owned_by_separate_signer_and_node_layers(self) -> None:
        signer_overlay = load_one(APPS / "kustomization.yaml")
        self.assertEqual(
            signer_overlay["resources"],
            [
                "../base/web3signer",
                "../base/aws-rds-ca",
                "../base/ephemery-162-network-config",
                "profile.yaml",
                "validator-keystore-secret.yaml",
            ],
        )
        self.assertNotIn("HelmRelease", yaml.safe_dump(signer_overlay))

        node_overlay = load_one(NODE_APPS / "kustomization.yaml")
        self.assertIn("../../local/assignments", node_overlay["resources"])
        self.assertIn("sync-dashboard.yaml", node_overlay["resources"])
        self.assertNotIn("../base/web3signer", node_overlay["resources"])

    def test_dev_desired_state_contains_no_gke_contract(self) -> None:
        roots = (
            CLUSTER,
            CONTROLLERS,
            CONFIGS,
            SIGNER_CONFIGS,
            PREREQUISITES,
            APPS,
            NODE_APPS,
        )
        for root in roots:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    with self.subTest(path=path):
                        self.assertNotIn("gke", path.read_text(encoding="utf-8").lower())

    def test_github_actions_have_no_aws_or_cluster_mutation_path(self) -> None:
        workflow_text = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in sorted((*WORKFLOWS.glob("*.yaml"), *WORKFLOWS.glob("*.yml")))
        )
        for forbidden in (
            "aws-actions/configure-aws-credentials",
            "id-token: write",
            "terraform apply",
            "kubectl apply",
            "kubectl delete",
            "flux bootstrap",
            "aws eks update",
            "kubeconfig",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow_text)

    def test_rendered_eks_prerequisites_preserve_tls_and_username(self) -> None:
        documents = render_all(PREREQUISITES)
        job = object_named(documents, "Job", "web3signer-schema-v12")
        flyway = next(container for container in job["spec"]["template"]["spec"]["containers"] if container["name"] == "flyway")
        env = {entry["name"]: entry for entry in flyway["env"]}
        self.assertIn("sslmode=verify-full", env["FLYWAY_URL"]["value"])
        self.assertIn(
            "sslrootcert=/var/run/aws-rds-ca/rds-ca.pem",
            env["FLYWAY_URL"]["value"],
        )
        self.assertEqual(env["FLYWAY_USER"]["valueFrom"]["secretKeyRef"]["key"], "username")
        self.assertIn(
            {"name": "aws-rds-ca", "mountPath": "/var/run/aws-rds-ca", "readOnly": True},
            flyway["volumeMounts"],
        )
        self.assertIn(
            {"name": "aws-rds-ca", "configMap": {"name": "aws-rds-ca"}},
            job["spec"]["template"]["spec"]["volumes"],
        )
        ca = object_named(documents, "ConfigMap", "aws-rds-ca")
        self.assertEqual(ca["metadata"]["namespace"], "database")
        self.assertIn("-----BEGIN CERTIFICATE-----", ca["data"]["rds-ca.pem"])

        jdbc_urls = [
            value
            for document in documents
            for value in re.findall(r"jdbc:[^\s\"']+", yaml.safe_dump(document))
        ]
        self.assertTrue(jdbc_urls)
        self.assertTrue(all("sslmode=verify-full" in value for value in jdbc_urls))

        assert_rds_and_dns_egress(
            self,
            object_named(documents, "NetworkPolicy", "web3signer-schema"),
        )

    def test_rendered_eks_apps_preserve_tls_username_and_network_contract(self) -> None:
        documents = render_all(APPS)
        deployment = object_named(documents, "Deployment", "web3signer")
        args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        jdbc_urls = [arg for arg in args if arg.startswith("--slashing-protection-db-url=")]
        self.assertEqual(len(jdbc_urls), 1)
        self.assertIn("sslmode=verify-full", jdbc_urls[0])
        self.assertIn("sslrootcert=/var/run/aws-rds-ca/rds-ca.pem", jdbc_urls[0])
        self.assertIn("--slashing-protection-db-username=$(DB_USERNAME)", args)
        web3signer = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertIn(
            {"name": "aws-rds-ca", "mountPath": "/var/run/aws-rds-ca", "readOnly": True},
            web3signer["volumeMounts"],
        )
        self.assertIn(
            {"name": "aws-rds-ca", "configMap": {"name": "aws-rds-ca"}},
            deployment["spec"]["template"]["spec"]["volumes"],
        )
        ca = object_named(documents, "ConfigMap", "aws-rds-ca")
        self.assertEqual(ca["metadata"]["namespace"], "signing")
        self.assertIn("-----BEGIN CERTIFICATE-----", ca["data"]["rds-ca.pem"])

        rendered_urls = [
            value
            for document in documents
            for value in re.findall(r"jdbc:[^\s\"']+", yaml.safe_dump(document))
        ]
        self.assertTrue(rendered_urls)
        self.assertTrue(all("sslmode=verify-full" in value for value in rendered_urls))
        assert_rds_and_dns_egress(
            self,
            object_named(documents, "NetworkPolicy", "web3signer"),
        )

    def test_runbook_keeps_runtime_gates_manual_and_sequenced(self) -> None:
        runbook = (ROOT / "docs" / "runbooks" / "eks-flux-bootstrap.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Do not remove both suspensions in one change", runbook)
        self.assertIn("read_only: true", runbook)
        self.assertIn("sslmode=verify-full", runbook)
        self.assertRegex(runbook, r"reports exactly\s+the deposited public keys")
        self.assertIn("--validator-id validator-ephemery-162-02", runbook)
        self.assertIn("GitHub Actions", runbook)
        self.assertIn("Do not pass `--allow-write`", runbook)
        self.assertNotIn("flux bootstrap github", runbook)
        self.assertIn('export PATH="$PWD/.local/bin:$PATH"', runbook)
        self.assertIn('enableNetworkPolicy == "true"', runbook)
        self.assertIn('ENABLE_POD_ENI == "true"', runbook)
        self.assertIn("NETWORK_POLICY_ENFORCING_MODE", runbook)
        self.assertIn("POD_SECURITY_GROUP_ENFORCING_MODE", runbook)
        self.assertIn("vpc.amazonaws.com/pod-eni", runbook)
        self.assertNotIn("vpc.amazonaws.com/has-trunk-attached", runbook)
        self.assertIn("eks-network-policy-probe.yaml", runbook)
        self.assertIn("ALLOWED_OUTPUT", runbook)
        self.assertIn("DENIED_STATUS", runbook)
        self.assertIn("docs/evidence/", runbook)
        self.assertIn("aws-k8s-branch-eni", runbook)
        self.assertIn("WEB3SIGNER_MIGRATION_POD_SECURITY_GROUP_ID", runbook)
        self.assertIn("WEB3SIGNER_POD_SECURITY_GROUP_ID", runbook)
        self.assertIn("postBuild.substituteFrom", runbook)
        self.assertIn("StrictPostBuildSubstitutions=true", runbook)
        self.assertIn("missing signer-only key fails", runbook)
        self.assertIn("applied", runbook)
        self.assertNotIn("network-policy-deny-probe", runbook)

        evidence_contract = (ROOT / "docs" / "evidence" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("uncommitted terminal scrollback is not runtime evidence", evidence_contract)
        self.assertIn("security-group", evidence_contract)


if __name__ == "__main__":
    unittest.main()
