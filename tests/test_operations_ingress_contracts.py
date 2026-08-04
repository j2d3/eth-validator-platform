"""Contracts for the exact-hostname HTTPS operations ingress."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DNS = ROOT / "terraform" / "environments" / "dns"
CONTROLLERS = ROOT / "platform" / "infrastructure" / "controllers"
DEV_CONTROLLERS = ROOT / "platform" / "infrastructure" / "overlays" / "dev" / "controllers"
CLUSTER = ROOT / "clusters" / "dev"
RUNBOOK = ROOT / "docs" / "runbooks" / "operations-ingress.md"
BOOTSTRAP = ROOT / "docs" / "runbooks" / "eks-flux-bootstrap.md"


class OperationsDnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.main = (DNS / "main.tf").read_text(encoding="utf-8")
        self.variables = (DNS / "variables.tf").read_text(encoding="utf-8")
        self.outputs = (DNS / "outputs.tf").read_text(encoding="utf-8")

    def test_certificate_and_dns_use_only_the_exact_operations_hostname(self) -> None:
        self.assertIn('operations_hostname = "ops.g.j2d3.com"', self.main)
        self.assertNotIn("*.j2d3.com", self.main)
        self.assertIn('resource "aws_acm_certificate" "operations"', self.main)
        self.assertIn("validation_method = \"DNS\"", self.main)
        self.assertIn(
            'resource "aws_acm_certificate_validation" "operations"', self.main
        )

    def test_nlb_record_is_absent_until_an_observed_aws_hostname_is_supplied(self) -> None:
        self.assertIn(
            "count = var.operations_load_balancer_hostname == null ? 0 : 1",
            self.main,
        )
        self.assertIn("elb\\\\.[a-z0-9-]+\\\\.amazonaws", self.variables)
        self.assertIn('type            = "CNAME"', self.main)
        self.assertIn("operations_dns_record", self.outputs)


class OperationsIngressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = yaml.safe_load(
            (DEV_CONTROLLERS / "ingress-nginx.yaml").read_text(encoding="utf-8")
        )
        self.values = self.release["spec"]["values"]

    def test_release_is_chart_pinned_and_requires_exact_certificate_input(self) -> None:
        chart = self.release["spec"]["chart"]["spec"]
        self.assertEqual((chart["chart"], chart["version"]), ("ingress-nginx", "4.15.1"))
        service = self.values["controller"]["service"]
        self.assertEqual(service["type"], "LoadBalancer")
        self.assertFalse(service["enableHttp"])
        self.assertTrue(service["enableHttps"])
        self.assertEqual(service["targetPorts"], {"https": "http"})
        self.assertEqual(
            service["annotations"][
                "service.beta.kubernetes.io/aws-load-balancer-ssl-cert"
            ],
            "${OPERATIONS_ACM_CERTIFICATE_ARN}",
        )
        self.assertEqual(
            service["annotations"][
                "service.beta.kubernetes.io/aws-load-balancer-ssl-ports"
            ],
            "https",
        )

    def test_one_public_nlb_runs_two_controllers_on_system_nodes(self) -> None:
        controller = self.values["controller"]
        service = controller["service"]
        self.assertEqual(controller["replicaCount"], 2)
        self.assertEqual(controller["minAvailable"], 1)
        self.assertEqual(controller["nodeSelector"]["workload"], "system")
        self.assertEqual(
            service["annotations"][
                "service.beta.kubernetes.io/aws-load-balancer-type"
            ],
            "nlb",
        )
        self.assertEqual(
            service["annotations"][
                "service.beta.kubernetes.io/aws-load-balancer-scheme"
            ],
            "internet-facing",
        )
        self.assertEqual(service["loadBalancerSourceRanges"], ["0.0.0.0/0"])

    def test_ingress_class_and_webhook_fail_closed(self) -> None:
        controller = self.values["controller"]
        self.assertFalse(controller["allowSnippetAnnotations"])
        self.assertFalse(controller["watchIngressWithoutClass"])
        self.assertFalse(controller["ingressClassResource"]["default"])
        self.assertTrue(controller["admissionWebhooks"]["enabled"])
        self.assertEqual(controller["admissionWebhooks"]["failurePolicy"], "Fail")

    def test_flux_requires_nonsecret_certificate_arn_substitution(self) -> None:
        layer = yaml.safe_load(
            (CLUSTER / "infrastructure-controllers.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            layer["spec"]["postBuild"]["substituteFrom"],
            [{"kind": "ConfigMap", "name": "aws-ingress-inputs", "optional": False}],
        )
        bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn("create configmap aws-ingress-inputs", bootstrap)
        self.assertIn("OPERATIONS_ACM_CERTIFICATE_ARN", bootstrap)

    def test_runbook_records_cost_two_phase_dns_and_removal(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        for value in (
            "hourly and capacity-unit charge",
            "with no NLB hostname",
            ".elb.*.amazonaws.com",
            "Removing or suspending application Pods does not stop the NLB charge",
        ):
            with self.subTest(value=value):
                self.assertIn(value, runbook)

    def test_ingress_controller_is_eks_only(self) -> None:
        base = (CONTROLLERS / "kustomization.yaml").read_text(encoding="utf-8")
        overlay = (DEV_CONTROLLERS / "kustomization.yaml").read_text(encoding="utf-8")
        self.assertNotIn("ingress-nginx", base)
        for resource in (
            "ingress-nginx-namespace.yaml",
            "ingress-nginx-repository.yaml",
            "ingress-nginx.yaml",
        ):
            self.assertIn(resource, overlay)


if __name__ == "__main__":
    unittest.main()
