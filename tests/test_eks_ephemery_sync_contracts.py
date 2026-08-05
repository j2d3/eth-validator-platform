"""Offline contracts for the Ephemery Geth/Lighthouse assignment on EKS.

These tests prove desired-state composition, not AWS provisioning, P2P
reachability, peer discovery, or chain sync. Runtime evidence remains a runbook
gate and must not be inferred from a green render.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tools import render_local_assignments


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "ethereum-node"
EKS_VALUES = CHART / "values-eks-ephemery.yaml"
NODE_APPS = ROOT / "platform" / "apps" / "nodes" / "dev"
CLUSTER = ROOT / "clusters" / "dev"
RUNBOOK = ROOT / "docs" / "runbooks" / "eks-ephemery-sync.md"


def load_documents(text: str) -> list[dict]:
    return [document for document in yaml.safe_load_all(text) if document]


class EksEphemeryRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        catalog = render_local_assignments.load_catalog()
        release = render_local_assignments.build_release(
            "assignment-ephemery-162-synthetic", catalog
        )
        release["spec"]["values"]["engineJwt"] = {
            "secretStoreName": "aws-engine-secrets",
            "remoteSecretKey": "eth-validator-platform-dev/ethereum/engine-jwt",
            "remoteSecretProperty": "jwt.hex",
        }
        release["spec"]["values"]["telemetry"] = {
            "cluster": "eth-validator-platform-dev",
            "environment": "dev",
        }
        # The shared EKS values file keeps client-diversity pairs on
        # ClusterIP. The dev overlay promotes only this selected pair to the
        # one billed public NLB.
        release["spec"]["values"]["p2p"] = {
            "service": {
                "enabled": True,
                "nameSuffix": "p2p-nlb",
                "type": "LoadBalancer",
                "loadBalancerClass": "service.k8s.aws/nlb",
                "annotations": {
                    "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type": "ip",
                    "service.beta.kubernetes.io/aws-load-balancer-enable-tcp-udp-listener": "true",
                    "service.beta.kubernetes.io/aws-load-balancer-scheme": "internet-facing",
                    "service.beta.kubernetes.io/aws-load-balancer-healthcheck-protocol": "tcp",
                    "service.beta.kubernetes.io/aws-load-balancer-healthcheck-port": "9000",
                    "service.beta.kubernetes.io/aws-load-balancer-attributes": "load_balancing.cross_zone.enabled=true",
                },
                "externalTrafficPolicy": "Cluster",
                "loadBalancerSourceRanges": ["0.0.0.0/0"],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            projected_values = Path(directory) / "values.yaml"
            projected_values.write_text(
                yaml.safe_dump(release["spec"]["values"], sort_keys=False),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "helm",
                    "template",
                    "ephemery-eks",
                    str(CHART),
                    "--namespace",
                    "ethereum",
                    "--values",
                    str(EKS_VALUES),
                    "--values",
                    str(projected_values),
                    "--set",
                    "lifecycleState=active",
                    "--set",
                    "telemetry.cluster=eth-validator-platform-dev",
                    "--set",
                    "telemetry.environment=dev",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise AssertionError(result.stderr)
        cls.documents = load_documents(result.stdout)
        cls.by_kind: dict[str, list[dict]] = {}
        for document in cls.documents:
            cls.by_kind.setdefault(document["kind"], []).append(document)

    def test_renders_one_node_pair_and_one_remote_signing_validator(self) -> None:
        self.assertEqual(len(self.by_kind["StatefulSet"]), 1)
        self.assertEqual(len(self.by_kind["Deployment"]), 1)
        validator = self.by_kind["Deployment"][0]
        self.assertTrue(validator["metadata"]["name"].endswith("-validator"))
        args = validator["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertIn("--testnet-dir=/validator-network", args)
        self.assertIn("--init-slashing-protection", args)
        self.assertIn("--enable-doppelganger-protection", args)
        self.assertIn("--disable-slashing-protection-web3signer", args)
        text = yaml.safe_dump_all(self.documents).lower()
        self.assertNotIn("validator-keystore", text)
        self.assertNotIn("signingsecretref", text)
        self.assertNotIn("web3signer-database", text)
        self.assertIn(
            "http://web3signer.signing.svc.cluster.local:9000",
            text,
        )

        network_source_volume = next(
            volume
            for volume in validator["spec"]["template"]["spec"]["volumes"]
            if volume["name"] == "validator-network-source"
        )
        self.assertEqual(
            network_source_volume["configMap"]["items"],
            [
                {"key": "config.yaml", "path": "config.yaml"},
                {
                    "key": "deposit_contract_block.txt",
                    "path": "deposit_contract_block.txt",
                },
            ],
        )
        network_volume = next(
            volume
            for volume in validator["spec"]["template"]["spec"]["volumes"]
            if volume["name"] == "validator-network"
        )
        self.assertEqual(network_volume["emptyDir"]["sizeLimit"], "16Mi")

        init_containers = {
            container["name"]: container
            for container in validator["spec"]["template"]["spec"]["initContainers"]
        }
        self.assertEqual(
            set(init_containers),
            {
                "configure-validator",
                "fetch-validator-genesis",
                "verify-validator-network",
            },
        )
        fetch_command = init_containers["fetch-validator-genesis"]["args"][0]
        self.assertIn("/eth/v2/debug/beacon/states/genesis", fetch_command)
        self.assertIn("Accept: application/octet-stream", fetch_command)
        verify_command = init_containers["verify-validator-network"]["args"][0]
        self.assertIn("1785438600", verify_command)
        self.assertIn(
            "0xe7ba535e068e129a2e3b17ee6a8f275eee3d1a01126f583ea7b6e867a91c0e5e",
            verify_command,
        )

        external_secrets = self.by_kind["ExternalSecret"]
        self.assertEqual(len(external_secrets), 1)
        self.assertEqual(
            external_secrets[0]["spec"]["secretStoreRef"]["name"],
            "aws-engine-secrets",
        )

    def test_chain_claims_use_small_encrypted_gp3_generation_identity(self) -> None:
        claims = self.by_kind["PersistentVolumeClaim"]
        self.assertEqual(len(claims), 3)
        sizes = {
            claim["metadata"]["name"].rsplit("-", 1)[-1]: claim["spec"]["resources"][
                "requests"
            ]["storage"]
            for claim in claims
        }
        self.assertEqual(
            sizes,
            {"execution": "50Gi", "consensus": "20Gi", "validator": "5Gi"},
        )
        for claim in claims:
            with self.subTest(claim=claim["metadata"]["name"]):
                self.assertEqual(claim["spec"]["storageClassName"], "ebs-gp3-encrypted")
                self.assertIn("1607eeafd183", claim["metadata"]["name"])
                self.assertEqual(
                    claim["metadata"]["annotations"][
                        "platform.galaxy-lab/network-identity"
                    ],
                    "1607eeafd1831115cd81bfd3aed07ea9a154ec688776a25f3395c960756a048c",
                )

    def test_geth_uses_validated_full_sync_only_in_the_eks_ephemery_profile(
        self,
    ) -> None:
        stateful_set = self.by_kind["StatefulSet"][0]
        execution = next(
            container
            for container in stateful_set["spec"]["template"]["spec"]["containers"]
            if container["name"] == "execution"
        )
        self.assertEqual(execution["command"], ["/bin/sh", "-ec"])
        self.assertIn("--syncmode=full", execution["args"][0])

        defaults = yaml.safe_load(
            (CHART / "values.yaml").read_text(encoding="utf-8")
        )
        eks_profile = yaml.safe_load(EKS_VALUES.read_text(encoding="utf-8"))
        self.assertEqual(defaults["executionClients"]["geth"]["syncMode"], "snap")
        self.assertEqual(
            eks_profile["executionClients"]["geth"]["syncMode"], "full"
        )

        schema = json.loads(
            (CHART / "values.schema.json").read_text(encoding="utf-8")
        )
        self.assertIn("executionClients", schema["required"])
        sync_mode = schema["properties"]["executionClients"]["properties"][
            "geth"
        ]["properties"]["syncMode"]
        self.assertEqual(sync_mode["enum"], ["snap", "full"])

    def test_selected_p2p_pair_uses_aws_lbc_without_exposing_http_or_metrics(
        self,
    ) -> None:
        services = self.by_kind["Service"]
        p2p = next(
            service
            for service in services
            if service["metadata"]["name"].endswith("-p2p-nlb")
        )
        internal = next(service for service in services if service is not p2p)

        self.assertEqual(p2p["spec"]["type"], "LoadBalancer")
        self.assertEqual(p2p["spec"]["externalTrafficPolicy"], "Cluster")
        self.assertEqual(p2p["spec"]["loadBalancerClass"], "service.k8s.aws/nlb")
        self.assertEqual(p2p["spec"]["loadBalancerSourceRanges"], ["0.0.0.0/0"])
        annotations = p2p["metadata"]["annotations"]
        self.assertNotIn("service.beta.kubernetes.io/aws-load-balancer-type", annotations)
        self.assertEqual(
            annotations[
                "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type"
            ],
            "ip",
        )
        self.assertEqual(
            annotations[
                "service.beta.kubernetes.io/aws-load-balancer-healthcheck-port"
            ],
            "9000",
        )
        self.assertEqual(
            annotations[
                "service.beta.kubernetes.io/aws-load-balancer-enable-tcp-udp-listener"
            ],
            "true",
        )
        p2p_ports = {
            (port["port"], port.get("protocol", "TCP")) for port in p2p["spec"]["ports"]
        }
        self.assertEqual(
            p2p_ports,
            {
                (30303, "TCP"),
                (30303, "UDP"),
                (9000, "TCP"),
                (9000, "UDP"),
                (9001, "UDP"),
            },
        )
        self.assertTrue(all("nodePort" not in port for port in p2p["spec"]["ports"]))
        self.assertEqual(internal["spec"]["type"], "ClusterIP")
        self.assertEqual(
            {port["port"] for port in internal["spec"]["ports"]},
            {5052, 6060, 8008},
        )

    def test_restricted_runtime_and_spot_preference_fit_one_bounded_worker(
        self,
    ) -> None:
        pod_spec = self.by_kind["StatefulSet"][0]["spec"]["template"]["spec"]
        security = pod_spec["securityContext"]
        self.assertTrue(security["runAsNonRoot"])
        self.assertEqual((security["runAsUser"], security["runAsGroup"]), (1000, 1000))
        self.assertEqual(security["seccompProfile"]["type"], "RuntimeDefault")
        self.assertEqual(security["fsGroupChangePolicy"], "OnRootMismatch")

        self.assertEqual(pod_spec["nodeSelector"], {"workload": "ethereum"})
        self.assertEqual(pod_spec["terminationGracePeriodSeconds"], 30)
        pdbs = self.by_kind["PodDisruptionBudget"]
        self.assertEqual(len(pdbs), 1)
        self.assertEqual(
            pdbs[0]["spec"]["selector"]["matchLabels"][
                "platform.galaxy-lab/component"
            ],
            "validator",
        )
        preference = pod_spec["affinity"]["nodeAffinity"][
            "preferredDuringSchedulingIgnoredDuringExecution"
        ][0]
        self.assertEqual(preference["weight"], 100)
        expression = preference["preference"]["matchExpressions"][0]
        self.assertEqual(expression["key"], "eks.amazonaws.com/capacityType")
        self.assertEqual(expression["values"], ["SPOT"])

        expected_limits = {"execution": ("5", "24Gi"), "consensus": ("3", "16Gi")}
        for container in pod_spec["containers"]:
            with self.subTest(container=container["name"]):
                context = container["securityContext"]
                self.assertTrue(context["runAsNonRoot"])
                self.assertFalse(context["allowPrivilegeEscalation"])
                self.assertTrue(context["readOnlyRootFilesystem"])
                self.assertIn("ALL", context["capabilities"]["drop"])
                self.assertEqual(
                    (
                        container["resources"]["limits"]["cpu"],
                        container["resources"]["limits"]["memory"],
                    ),
                    expected_limits[container["name"]],
                )
                self.assertIn("startupProbe", container)
                self.assertIn("readinessProbe", container)
                self.assertIn("livenessProbe", container)
                self.assertIn(
                    {"name": f"{container['name']}-runtime", "mountPath": "/tmp"},
                    container["volumeMounts"],
                )

        runtime_volumes = {
            volume["name"]: volume["emptyDir"]["sizeLimit"]
            for volume in pod_spec["volumes"]
            if volume["name"].endswith("-runtime")
        }
        self.assertEqual(
            runtime_volumes,
            {"execution-runtime": "256Mi", "consensus-runtime": "256Mi"},
        )

    def test_network_policy_opens_only_p2p_publicly(self) -> None:
        policy = self.by_kind["NetworkPolicy"][0]
        public_rule = policy["spec"]["ingress"][0]
        self.assertNotIn("from", public_rule)
        self.assertEqual(
            {(port["port"], port["protocol"]) for port in public_rule["ports"]},
            {
                (30303, "TCP"),
                (30303, "UDP"),
                (9000, "TCP"),
                (9000, "UDP"),
                (9001, "UDP"),
            },
        )
        public_ports = {port["port"] for port in public_rule["ports"]}
        self.assertTrue({5052, 6060, 8008, 8545, 8551}.isdisjoint(public_ports))

    def test_consensus_peer_rule_sums_verified_client_series(
        self,
    ) -> None:
        rules = self.by_kind["PrometheusRule"][0]["spec"]["groups"][0]["rules"]
        peer_rule = next(
            rule
            for rule in rules
            if rule.get("record") == "validator_platform_consensus_peers"
        )
        self.assertIn("sum by", peer_rule["expr"])
        # The rule takes an `or`-union across every declared CL adapter's
        # documented peer series; the assignment-under-test only selects
        # Lighthouse, but the chart renders both adapters' contributions
        # so a future Teku-selected pair inherits the same PrometheusRule.
        # Both contributions are filtered by consensus_client="…" so a pair
        # never double-counts its peers.
        self.assertIn("sync_peers_per_status", peer_rule["expr"])
        self.assertIn("libp2p_peers", peer_rule["expr"])
        self.assertIn('consensus_client="lighthouse"', peer_rule["expr"])
        self.assertIn('consensus_client="teku"', peer_rule["expr"])

    def test_validator_rule_uses_the_observed_lighthouse_metric(self) -> None:
        rules = self.by_kind["PrometheusRule"][0]["spec"]["groups"][0]["rules"]
        validator_rule = next(
            rule
            for rule in rules
            if rule.get("record") == "validator_platform_validator_enabled"
        )
        self.assertIn("vc_validators_enabled_count", validator_rule["expr"])
        self.assertNotIn("validator_enabled_count{", validator_rule["expr"])

    def test_lighthouse_receives_digest_verified_ephemery_bootnodes(self) -> None:
        stateful_set = self.by_kind["StatefulSet"][0]
        consensus = next(
            container
            for container in stateful_set["spec"]["template"]["spec"]["containers"]
            if container["name"] == "consensus"
        )
        self.assertEqual(consensus["command"], ["/bin/sh", "-ec"])
        command = consensus["args"][0]
        self.assertIn(
            'paste -sd, "/network/files/boot_enr.txt"',
            command,
        )
        self.assertIn('--boot-nodes="$bootnodes"', command)
        self.assertIn("--testnet-dir=/network/files", command)
        self.assertIn(
            "--checkpoint-sync-url=https://checkpoint-sync.ephemery.ethpandaops.io/",
            command,
        )


class EksEphemeryFluxAndTelemetryTests(unittest.TestCase):
    def test_signing_node_layer_waits_for_signer_application(self) -> None:
        layer = yaml.safe_load((CLUSTER / "node-apps.yaml").read_text(encoding="utf-8"))
        self.assertEqual(
            layer["spec"]["dependsOn"], [{"name": "apps"}]
        )
        # The layer remains reconciled; its dependency now orders the validator
        # behind the Ready shared-signer application.
        self.assertIn("suspend", layer["spec"])
        self.assertFalse(layer["spec"]["suspend"])

        rendered = subprocess.run(
            ["kubectl", "kustomize", str(NODE_APPS)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        documents = load_documents(rendered)
        network_config = next(
            document
            for document in documents
            if document["kind"] == "ConfigMap"
            and document["metadata"]["name"]
            == "web3signer-network-config-ephemery-162"
        )
        self.assertEqual(network_config["metadata"]["namespace"], "ethereum")
        self.assertEqual(network_config["data"]["deposit_contract_block.txt"], "0\n")
        releases = [
            document for document in documents if document["kind"] == "HelmRelease"
        ]
        # Seven generation-pinned Ephemery pairs are rendered by this overlay:
        # Geth+Lighthouse, Reth+Lighthouse, Geth+Teku, and Reth+Teku sign with
        # disjoint identities; Erigon+Lighthouse, Geth+Nimbus, and Besu+Teku
        # stay non-signing. Everything else in this test asserts on the four
        # signing pairs; the non-signing pairs are covered in
        # test_chart_reth_adapter_contracts, test_chart_teku_adapter_contracts,
        # test_chart_erigon_adapter_contracts, test_chart_besu_adapter_contracts,
        # test_chart_nimbus_adapter_contracts,
        # and test_local_assignment_projection.
        self.assertEqual(
            sorted(release["metadata"]["name"] for release in releases),
            [
                "assignment-ephemery-162-synthetic",
                "assignment-ephemery-162-synthetic-besu-teku",
                "assignment-ephemery-162-synthetic-erigon",
                "assignment-ephemery-162-synthetic-geth-nimbus",
                "assignment-ephemery-162-synthetic-reth",
                "assignment-ephemery-162-synthetic-reth-teku",
                "assignment-ephemery-162-synthetic-teku",
            ],
        )
        # Every rendered Ephemery release must carry the same EKS-specific
        # inputs (valuesFiles, dev telemetry, aws-engine-secrets Engine JWT).
        # A missing patch on any release would leave that pair pointing at
        # the local kind defaults (standard StorageClass, local-platform-
        # secrets, kind-eth-validator-local telemetry) on EKS — an outage,
        # not an obviously-broken render.
        for release in releases:
            with self.subTest(release=release["metadata"]["name"]):
                self.assertEqual(release["spec"]["values"]["lifecycleState"], "active")
                self.assertEqual(
                    release["spec"]["values"]["engineJwt"]["secretStoreName"],
                    "aws-engine-secrets",
                )
                self.assertEqual(
                    release["spec"]["values"]["telemetry"],
                    {
                        "cluster": "eth-validator-platform-dev",
                        "environment": "dev",
                    },
                )
                # Flux resolves valuesFiles from the GitRepository artifact
                # root when the chart source is a GitRepository, so entries
                # include the chart directory prefix (verified against
                # source-controller v1.8.5 in dev on 2026-08-04).
                self.assertEqual(
                    release["spec"]["chart"]["spec"]["valuesFiles"],
                    [
                        "charts/ethereum-node/values.yaml",
                        "charts/ethereum-node/values-eks-ephemery.yaml",
                    ],
                )

        public_p2p = [
            release
            for release in releases
            if release["spec"]["values"].get("p2p", {}).get("service", {}).get("type")
            == "LoadBalancer"
        ]
        self.assertEqual(
            [release["metadata"]["name"] for release in public_p2p],
            ["assignment-ephemery-162-synthetic"],
        )
        self.assertEqual(
            public_p2p[0]["spec"]["values"]["p2p"]["service"][
                "loadBalancerClass"
            ],
            "service.k8s.aws/nlb",
        )

        signing_releases = {
            release["metadata"]["name"]: release
            for release in releases
            if release["spec"]["values"]["validator"]["enabled"]
        }
        self.assertEqual(
            set(signing_releases),
            {
                "assignment-ephemery-162-synthetic",
                "assignment-ephemery-162-synthetic-reth",
                "assignment-ephemery-162-synthetic-reth-teku",
                "assignment-ephemery-162-synthetic-teku",
            },
        )
        signing_public_keys = set()
        signing_validator_ids = set()
        for release in signing_releases.values():
            values = release["spec"]["values"]
            validator = values["validator"]
            self.assertTrue(validator["slashingProtectionConfirmed"])
            self.assertTrue(
                values["networkProfile"]["signer"]["web3signer"]["signingQualified"]
            )
            self.assertEqual(
                validator["networkConfigMapName"],
                "web3signer-network-config-ephemery-162",
            )
            signing_public_keys.add(validator["publicKey"])
            signing_validator_ids.add(values["identity"]["validatorId"])
        self.assertEqual(len(signing_public_keys), 4)
        self.assertEqual(
            signing_validator_ids,
            {
                "validator-ephemery-162-01",
                "validator-ephemery-162-02",
                "validator-ephemery-162-03",
                "validator-ephemery-162-04",
            },
        )

    def test_sync_dashboard_uses_only_declared_evidence_and_states_limits(self) -> None:
        config_map = yaml.safe_load(
            (NODE_APPS / "sync-dashboard.yaml").read_text(encoding="utf-8")
        )
        dashboard = json.loads(config_map["data"]["eks-ephemery-sync.json"])
        self.assertEqual(dashboard["uid"], "eth-eks-ephemery-sync")
        self.assertFalse(dashboard["editable"])
        self.assertEqual(
            len({panel["id"] for panel in dashboard["panels"]}),
            len(dashboard["panels"]),
        )

        expressions = [
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        ]
        # Pair-scoped rules live in the chart; tier-scoped signer rules live in
        # the shared web3signer base. A dashboard metric is verified against
        # whichever file declares it — inventing a metric name in either file
        # would still fail this join.
        rules = (CHART / "templates" / "prometheusrule.yaml").read_text(
            encoding="utf-8"
        ) + (
            ROOT
            / "platform"
            / "apps"
            / "base"
            / "web3signer"
            / "prometheusrule.yaml"
        ).read_text(encoding="utf-8")
        platform_metrics = {
            token.split("{")[0]
            for expression in expressions
            for token in expression.replace("(", " ").split()
            if token.startswith("validator_platform_")
        }
        for metric in platform_metrics:
            with self.subTest(metric=metric):
                self.assertIn(f"record: {metric}", rules)

        text = json.dumps(dashboard).lower()
        self.assertIn("kubernetes readiness reports", text)
        self.assertIn("not an independent network-tip distance", text)
        self.assertNotIn("signing remain disabled", text)
        self.assertNotIn("public_key", text)
        # Signing-lane panels intentionally reference Web3Signer.
        self.assertIn("web3signer", text)
        # Prevented signing requests remain explicitly labelled; a
        # green-on-increase misconfiguration is a review defect.
        self.assertIn("signing requests prevented", text)

    def test_runbook_requires_generation_capacity_p2p_and_sustained_sync_evidence(
        self,
    ) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "successor generation",
            "suspend: true",
            "validator.enabled=false",
            "deposited signing identity",
            "Engine JWT",
            "LoadBalancer",
            "loadBalancerClass: service.k8s.aws/nlb",
            "Pod-IP targets",
            "same Availability Zone",
            "30-second",
            "Spot interruption",
            "15-minute",
            "internal sync distance",
            "did not authorize validator duties",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
