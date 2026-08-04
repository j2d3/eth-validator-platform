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
STATUS_INGRESS = ROOT / "platform" / "apps" / "portal" / "dev" / "ingress.yaml"
GRAFANA_INGRESS = DEV_CONTROLLERS / "grafana-ingress.yaml"
MONITORING_PATCH = DEV_CONTROLLERS / "monitoring-patch.yaml"


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

    def test_status_and_grafana_use_only_reviewed_exact_paths(self) -> None:
        status = yaml.safe_load(STATUS_INGRESS.read_text(encoding="utf-8"))
        grafana = yaml.safe_load(GRAFANA_INGRESS.read_text(encoding="utf-8"))
        status_rule = status["spec"]["rules"][0]
        grafana_rule = grafana["spec"]["rules"][0]
        self.assertEqual(status_rule["host"], "ops.g.j2d3.com")
        self.assertEqual(grafana_rule["host"], "ops.g.j2d3.com")
        self.assertEqual(
            status_rule["http"]["paths"],
            [
                {
                    "path": "/api/status",
                    "pathType": "Exact",
                    "backend": {
                        "service": {
                            "name": "portal-status-api",
                            "port": {"name": "http"},
                        }
                    },
                }
            ],
        )
        self.assertEqual(
            grafana_rule["http"]["paths"][0]["path"], "/grafana"
        )
        self.assertEqual(
            grafana_rule["http"]["paths"][0]["pathType"], "Prefix"
        )

    def test_grafana_uses_https_subpath_and_grants_only_viewer_role_to_anonymous(self) -> None:
        # Anonymous read access is enabled for the demo. The chart values
        # explicitly cap it to the Viewer role: no dashboard editing,
        # datasource changes, or plugin install available without the admin
        # login. A production instance would replace this with OIDC/SSO
        # (issue #71).
        patch = yaml.safe_load(MONITORING_PATCH.read_text(encoding="utf-8"))
        config = patch["spec"]["values"]["grafana"]["grafana.ini"]
        self.assertEqual(
            config["server"],
            {
                "root_url": "https://ops.g.j2d3.com/grafana",
                "serve_from_sub_path": True,
            },
        )
        anon = config["auth.anonymous"]
        self.assertTrue(anon["enabled"])
        self.assertEqual(anon["org_role"], "Viewer")
        self.assertEqual(config["security"]["cookie_secure"], True)


if __name__ == "__main__":
    unittest.main()
