"""Behavioral contracts for the guarded EKS capacity operator command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hack" / "eks-lab-capacity.sh"

EMPTY_LIST = {"apiVersion": "v1", "items": [], "kind": "List"}

FAKE_AWS = r"""#!/usr/bin/env bash
set -euo pipefail
printf 'aws %s\n' "$*" >>"${FAKE_COMMAND_LOG}"

case "$*" in
  *"eks describe-cluster"*)
    printf '%s\n' 'https://fake.eks.example'
    ;;
  *"eks list-nodegroups"*)
    if [[ "${FAKE_FAIL_LIST_NODEGROUPS:-false}" == "true" ]]; then
      exit 44
    fi
    printf '%s\n' '{"nodegroups":["ethereum-a","ethereum-b","ethereum-c","system"]}'
    ;;
  *"eks describe-nodegroup"*)
    group=""
    previous=""
    for argument in "$@"; do
      if [[ "${previous}" == "--nodegroup-name" ]]; then
        group="${argument}"
        break
      fi
      previous="${argument}"
    done
    if [[ -n "${FAKE_FAIL_DESCRIBE_GROUP:-}" && "${group}" == "${FAKE_FAIL_DESCRIBE_GROUP}" ]]; then
      exit 45
    fi
    case "${group}" in
      ethereum-a) az=us-west-2a; desired="${FAKE_DESIRED_A:-0}" ;;
      ethereum-b) az=us-west-2b; desired="${FAKE_DESIRED_B:-0}" ;;
      ethereum-c) az=us-west-2c; desired="${FAKE_DESIRED_C:-0}" ;;
      system)
        printf '%s\n' '{"nodegroup":{"nodegroupName":"system","status":"ACTIVE","health":{"issues":[]},"capacityType":"ON_DEMAND","scalingConfig":{"minSize":2,"maxSize":4,"desiredSize":2},"labels":{"workload":"system"}}}'
        exit 0
        ;;
      *) exit 41 ;;
    esac
    printf '{"nodegroup":{"nodegroupName":"%s","status":"ACTIVE","health":{"issues":[]},"capacityType":"SPOT","scalingConfig":{"minSize":0,"maxSize":1,"desiredSize":%s},"labels":{"workload":"ethereum","platform.galaxy-lab/availability-zone":"%s"}}}\n' \
      "${group}" "${desired}" "${az}"
    ;;
  *"eks update-nodegroup-config"*)
    printf '%s\n' '{"update":{"id":"update-123","status":"InProgress"}}'
    ;;
  *"eks describe-update"*)
    printf '%s\n' 'Successful'
    ;;
  *)
    printf 'unexpected fake aws invocation: %s\n' "$*" >&2
    exit 42
    ;;
esac
"""

FAKE_KUBECTL = r"""#!/usr/bin/env bash
set -euo pipefail
printf 'kubectl %s\n' "$*" >>"${FAKE_COMMAND_LOG}"

if [[ "$*" == *"config view"* ]]; then
  printf '%s' 'https://fake.eks.example'
elif [[ "$*" == *"get configmap"* ]]; then
  printf '%s\n' "${FAKE_RECORDS_JSON}"
elif [[ "$*" == *"get pods"* ]]; then
  printf '%s\n' "${FAKE_PODS_JSON}"
elif [[ "$*" == *"get pvc"* ]]; then
  printf '%s\n' "${FAKE_PVCS_JSON}"
elif [[ "$*" == *"get pv "* ]]; then
  printf '%s\n' "${FAKE_PV_JSON}"
elif [[ "$*" == *"get nodes"* ]]; then
  printf '%s\n' "${FAKE_NODES_JSON}"
else
  printf 'unexpected fake kubectl invocation: %s\n' "$*" >&2
  exit 43
fi
"""


def lifecycle_record(*, state: str = "stopped", signing: bool = False) -> dict:
    value = "true" if signing else "false"
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "assignment-01-record",
            "namespace": "ethereum",
            "labels": {
                "app.kubernetes.io/part-of": "ethereum-validator-platform",
                "platform.galaxy-lab/signing-enabled": value,
            },
        },
        "data": {"lifecycleState": state, "validatorEnabled": value},
    }


def list_with(*items: dict) -> dict:
    return {"apiVersion": "v1", "items": list(items), "kind": "List"}


class EksCapacityOperationTests(unittest.TestCase):
    maxDiff = None

    def run_operator(
        self,
        *arguments: str,
        records: dict | None = None,
        pods: dict | None = None,
        pvcs: dict | None = None,
        pv: dict | None = None,
        nodes: dict | None = None,
        desired_a: int = 0,
        desired_b: int = 0,
        desired_c: int = 0,
        fail_list_nodegroups: bool = False,
        fail_describe_group: str = "",
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            aws = directory / "aws"
            kubectl = directory / "kubectl"
            kubeconfig = directory / "kubeconfig"
            command_log = directory / "commands.log"
            aws.write_text(FAKE_AWS, encoding="utf-8")
            kubectl.write_text(FAKE_KUBECTL, encoding="utf-8")
            aws.chmod(0o755)
            kubectl.chmod(0o755)
            kubeconfig.write_text("fake dedicated kubeconfig\n", encoding="utf-8")

            environment = os.environ.copy()
            environment.update(
                {
                    "AWS_BIN": str(aws),
                    "KUBECTL_BIN": str(kubectl),
                    "JQ_BIN": shutil.which("jq") or "jq",
                    "EKS_KUBECONFIG": str(kubeconfig),
                    "EKS_CAPACITY_TIMEOUT_SECONDS": "2",
                    "EKS_CAPACITY_POLL_SECONDS": "0",
                    "FAKE_COMMAND_LOG": str(command_log),
                    "FAKE_RECORDS_JSON": json.dumps(records or EMPTY_LIST),
                    "FAKE_PODS_JSON": json.dumps(pods or EMPTY_LIST),
                    "FAKE_PVCS_JSON": json.dumps(pvcs or EMPTY_LIST),
                    "FAKE_PV_JSON": json.dumps(pv or {}),
                    "FAKE_NODES_JSON": json.dumps(nodes or EMPTY_LIST),
                    "FAKE_DESIRED_A": str(desired_a),
                    "FAKE_DESIRED_B": str(desired_b),
                    "FAKE_DESIRED_C": str(desired_c),
                    "FAKE_FAIL_LIST_NODEGROUPS": str(fail_list_nodegroups).lower(),
                    "FAKE_FAIL_DESCRIBE_GROUP": fail_describe_group,
                }
            )
            result = subprocess.run(
                [str(SCRIPT), *arguments],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            log = command_log.read_text(encoding="utf-8")
            return result, log

    def test_pause_refuses_signing_enabled_desired_state(self) -> None:
        result, log = self.run_operator(
            "pause",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record(signing=True)),
            desired_a=1,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signing enabled", result.stderr)
        self.assertNotIn("update-nodegroup-config", log)

    def test_pause_refuses_an_active_lifecycle_even_without_signing(self) -> None:
        result, log = self.run_operator(
            "pause",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record(state="active")),
            desired_a=1,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not stopped or archived", result.stderr)
        self.assertNotIn("update-nodegroup-config", log)

    def test_pause_refuses_while_any_client_pod_exists(self) -> None:
        pod = {
            "metadata": {
                "name": "pair-01",
                "namespace": "ethereum",
                "labels": {"platform.galaxy-lab/component": "node"},
            }
        }
        result, log = self.run_operator(
            "pause",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            pods=list_with(pod),
            desired_a=1,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pods still exist", result.stderr)
        self.assertNotIn("update-nodegroup-config", log)

    def test_resume_refuses_a_different_bound_volume_zone(self) -> None:
        pvc = {
            "metadata": {"name": "pair-execution", "namespace": "ethereum"},
            "spec": {"volumeName": "pv-123"},
            "status": {"phase": "Bound"},
        }
        pv = {
            "spec": {
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "topology.kubernetes.io/zone",
                                        "operator": "In",
                                        "values": ["us-west-2b"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
        result, log = self.run_operator(
            "resume",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            pvcs=list_with(pvc),
            pv=pv,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resume the us-west-2b node group", result.stderr)
        self.assertNotIn("update-nodegroup-config", log)

    def test_resume_accepts_matching_ebs_csi_volume_zone(self) -> None:
        pvc = {
            "metadata": {"name": "pair-execution", "namespace": "ethereum"},
            "spec": {"volumeName": "pv-123"},
            "status": {"phase": "Bound"},
        }
        pv = {
            "spec": {
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "topology.ebs.csi.aws.com/zone",
                                        "operator": "In",
                                        "values": ["us-west-2a"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
        ready_node = {
            "metadata": {"name": "ip-10-0-0-1"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }

        result, log = self.run_operator(
            "resume",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            pvcs=list_with(pvc),
            pv=pv,
            nodes=list_with(ready_node),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("desiredSize=1", log)

    def test_resume_rejects_non_in_volume_affinity_operator(self) -> None:
        pvc = {
            "metadata": {"name": "pair-execution", "namespace": "ethereum"},
            "spec": {"volumeName": "pv-123"},
            "status": {"phase": "Bound"},
        }
        pv = {
            "spec": {
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "topology.ebs.csi.aws.com/zone",
                                        "operator": "NotIn",
                                        "values": ["us-west-2b"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }

        result, log = self.run_operator(
            "resume",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            pvcs=list_with(pvc),
            pv=pv,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no readable EBS Availability Zone", result.stderr)
        self.assertNotIn("update-nodegroup-config", log)

    def test_resume_refuses_when_another_ethereum_group_is_active(self) -> None:
        result, log = self.run_operator(
            "resume",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            desired_b=1,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another Ethereum node group", result.stderr)
        self.assertNotIn("update-nodegroup-config", log)

    def test_resume_refuses_when_any_group_description_is_missing(self) -> None:
        result, log = self.run_operator(
            "resume",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            desired_b=1,
            fail_describe_group="ethereum-b",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not enumerate Ethereum node groups", result.stderr)
        self.assertNotIn("update-nodegroup-config", log)

    def test_status_fails_loudly_when_group_listing_fails(self) -> None:
        result, log = self.run_operator("status", fail_list_nodegroups=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not enumerate Ethereum node groups", result.stderr)
        self.assertNotIn("AZ           NODE_GROUP", result.stdout)
        self.assertNotIn("get nodes", log)

    def test_pause_is_idempotent_only_after_safety_guards_pass(self) -> None:
        result, log = self.run_operator(
            "pause",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already paused", result.stdout)
        self.assertIn("get configmap", log)
        self.assertIn("get pods", log)
        self.assertNotIn("update-nodegroup-config", log)

    def test_pause_scales_one_group_to_zero_after_guards_pass(self) -> None:
        result, log = self.run_operator(
            "pause",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            desired_a=1,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Paused ethereum-a", result.stdout)
        self.assertIn("update-nodegroup-config", log)
        self.assertIn("desiredSize=0", log)
        self.assertIn("describe-update", log)
        self.assertIn("get nodes", log)
        self.assertEqual(log.count("eks describe-nodegroup"), 4)

    def test_resume_scales_one_group_and_waits_for_a_ready_node(self) -> None:
        ready_node = {
            "metadata": {"name": "ip-10-0-0-1"},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}]
            },
        }
        result, log = self.run_operator(
            "resume",
            "--az",
            "us-west-2a",
            records=list_with(lifecycle_record()),
            nodes=list_with(ready_node),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Resumed one Ready node", result.stdout)
        self.assertIn("update-nodegroup-config", log)
        self.assertIn("desiredSize=1", log)
        self.assertIn("describe-update", log)
        self.assertIn("get nodes", log)


if __name__ == "__main__":
    unittest.main()
