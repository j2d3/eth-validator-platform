"""Contracts for the EKS-only AWS Load Balancer Controller boundary."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEV_CONTROLLERS = (
    ROOT / "platform" / "infrastructure" / "overlays" / "dev" / "controllers"
)
RELEASE = DEV_CONTROLLERS / "aws-load-balancer-controller.yaml"
REPOSITORY = DEV_CONTROLLERS / "aws-load-balancer-controller-repository.yaml"
NODE_APPS = ROOT / "platform" / "apps" / "nodes" / "dev"
EKS_VALUES = ROOT / "charts" / "ethereum-node" / "values-eks-ephemery.yaml"
TERRAFORM = ROOT / "terraform" / "environments" / "dev"
BOOTSTRAP = ROOT / "docs" / "runbooks" / "eks-flux-bootstrap.md"


def load_documents(text: str) -> list[dict]:
    return [document for document in yaml.safe_load_all(text) if document]


class AwsLoadBalancerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
        self.values = self.release["spec"]["values"]

    def test_flux_release_is_exact_and_eks_only(self) -> None:
        chart = self.release["spec"]["chart"]["spec"]
        self.assertEqual(
            (chart["chart"], chart["version"]),
            ("aws-load-balancer-controller", "3.5.0"),
        )
        self.assertEqual(
            self.values["image"],
            {
                "repository": "public.ecr.aws/eks/aws-load-balancer-controller",
                "tag": "v3.5.0@sha256:298acdff5a571731276aaea3d5cc450a264e4ad710a5bddf3e518f68a3f9f6cb",
                "pullPolicy": "IfNotPresent",
            },
        )
        self.assertEqual(self.values["clusterName"], "${EKS_CLUSTER_NAME}")
        self.assertEqual(self.values["region"], "${AWS_REGION}")
        self.assertEqual(self.values["vpcId"], "${EKS_VPC_ID}")
        self.assertFalse(self.values["enableServiceMutatorWebhook"])
        self.assertFalse(self.values["createIngressClassResource"])
        self.assertEqual(
            self.values["serviceAccount"],
            {"create": True, "name": "aws-load-balancer-controller"},
        )
        self.assertEqual(self.values["nodeSelector"]["workload"], "system")
        self.assertEqual(self.values["replicaCount"], 2)
        self.assertEqual(self.values["podDisruptionBudget"], {"maxUnavailable": 1})

        overlay = yaml.safe_load(
            (DEV_CONTROLLERS / "kustomization.yaml").read_text(encoding="utf-8")
        )
        self.assertIn(RELEASE.name, overlay["resources"])
        self.assertIn(REPOSITORY.name, overlay["resources"])
        base = (
            ROOT / "platform" / "infrastructure" / "controllers" / "kustomization.yaml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("aws-load-balancer-controller", base)

    def test_pod_identity_is_bound_to_only_the_controller_service_account(self) -> None:
        terraform = (TERRAFORM / "load-balancer-controller.tf").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'resource "aws_eks_pod_identity_association" "load_balancer_controller"',
            terraform,
        )
        self.assertIn('namespace       = "kube-system"', terraform)
        self.assertIn(
            'service_account = "aws-load-balancer-controller"', terraform
        )
        self.assertIn(
            "role_arn        = aws_iam_role.load_balancer_controller.arn",
            terraform,
        )
        trust = (TERRAFORM / "main.tf").read_text(encoding="utf-8")
        self.assertIn('identifiers = ["pods.eks.amazonaws.com"]', trust)
        self.assertIn('actions = ["sts:AssumeRole", "sts:TagSession"]', trust)

        policy = json.loads(
            (
                TERRAFORM
                / "iam"
                / "aws-load-balancer-controller-v3.5.0.json"
            ).read_text(encoding="utf-8")
        )
        actions = {
            action
            for statement in policy["Statement"]
            for action in statement["Action"]
        }
        for required in (
            "elasticloadbalancing:CreateLoadBalancer",
            "elasticloadbalancing:CreateTargetGroup",
            "elasticloadbalancing:CreateListener",
            "elasticloadbalancing:RegisterTargets",
            "ec2:CreateSecurityGroup",
        ):
            self.assertIn(required, actions)

    def test_exactly_one_pair_requests_one_mixed_protocol_nlb(self) -> None:
        rendered = subprocess.run(
            ["kubectl", "kustomize", str(NODE_APPS)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        releases = [
            document
            for document in load_documents(rendered)
            if document["kind"] == "HelmRelease"
        ]
        public = [
            release
            for release in releases
            if release["spec"]["values"].get("p2p", {}).get("service", {}).get("type")
            == "LoadBalancer"
        ]
        self.assertEqual(
            [release["metadata"]["name"] for release in public],
            ["assignment-ephemery-162-synthetic"],
        )
        service = public[0]["spec"]["values"]["p2p"]["service"]
        self.assertEqual(service["nameSuffix"], "p2p-nlb")
        self.assertEqual(service["loadBalancerClass"], "service.k8s.aws/nlb")
        self.assertEqual(
            service["annotations"][
                "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type"
            ],
            "ip",
        )
        self.assertEqual(
            service["annotations"][
                "service.beta.kubernetes.io/aws-load-balancer-enable-tcp-udp-listener"
            ],
            "true",
        )
        self.assertNotIn(
            "service.beta.kubernetes.io/aws-load-balancer-type",
            service["annotations"],
        )
        default_profile = yaml.safe_load(EKS_VALUES.read_text(encoding="utf-8"))
        self.assertEqual(default_profile["p2p"]["service"]["type"], "ClusterIP")

    def test_flux_inputs_do_not_depend_on_node_metadata(self) -> None:
        runbook = BOOTSTRAP.read_text(encoding="utf-8")
        for key in (
            "EKS_CLUSTER_NAME",
            "AWS_REGION",
            "EKS_VPC_ID",
            "OPERATIONS_ACM_CERTIFICATE_ARN",
        ):
            self.assertIn(key, runbook)
        self.assertIn("load_balancer_controller_role_arn", runbook)


if __name__ == "__main__":
    unittest.main()
