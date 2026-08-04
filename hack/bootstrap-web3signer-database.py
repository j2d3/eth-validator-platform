#!/usr/bin/env python3
"""Create the restricted Web3Signer PostgreSQL credential without local files.

This trusted-local operator tool reads the RDS-managed master credential into
process memory, sends it to one temporary Kubernetes Secret over stdin, runs a
reviewed branch-ENI-qualified Job that creates/rotates a non-admin application
role, and writes only that application's connection JSON to the existing AWS
Secrets Manager container. Temporary Kubernetes objects are deleted on every
exit path. Secret values are never command-line arguments, files, or output.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "hack" / "qualification" / "web3signer-database-bootstrap.yaml"
NAMESPACE = "database"
RESOURCE_NAME = "web3signer-database-bootstrap"
CA_CONFIGMAP = "web3signer-rds-ca"
APP_USERNAME = "web3signer"
RDS_CA_URL = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
RDS_CA_SHA256 = "e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3"


class BootstrapError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    input_text: str | None = None,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=capture,
        check=check,
    )


def require_commands() -> None:
    missing = [name for name in ("aws", "kubectl", "terraform") if not shutil.which(name)]
    if missing:
        raise BootstrapError(f"missing required command(s): {', '.join(missing)}")


def terraform_output(root: Path, name: str) -> object:
    result = run(["terraform", f"-chdir={root}", "output", "-json", name])
    return json.loads(result.stdout)


def aws_json(args: list[str], *, input_object: object | None = None) -> object:
    input_text = None if input_object is None else json.dumps(input_object, separators=(",", ":"))
    result = run(["aws", *args], input_text=input_text)
    return json.loads(result.stdout)


def kubectl_json(args: list[str]) -> object:
    result = run(["kubectl", *args, "-o", "json"])
    return json.loads(result.stdout)


def apply_object(value: object) -> None:
    run(
        ["kubectl", "apply", "-f", "-"],
        input_text=json.dumps(value, separators=(",", ":")),
    )


def resource_exists(kind: str, name: str) -> bool:
    result = run(
        ["kubectl", "-n", NAMESPACE, "get", kind, name],
        capture=True,
        check=False,
    )
    return result.returncode == 0


def cleanup() -> None:
    # Names are constant and namespace-scoped; never use a broad selector.
    run(
        ["kubectl", "-n", NAMESPACE, "delete", "job", RESOURCE_NAME, "--ignore-not-found=true", "--wait=true"],
        check=False,
    )
    run(
        ["kubectl", "-n", NAMESPACE, "delete", "networkpolicy", RESOURCE_NAME, "--ignore-not-found=true", "--wait=true"],
        check=False,
    )
    run(
        ["kubectl", "-n", NAMESPACE, "delete", "secret", RESOURCE_NAME, "--ignore-not-found=true", "--wait=true"],
        check=False,
    )
    run(
        ["kubectl", "-n", NAMESPACE, "delete", "configmap", CA_CONFIGMAP, "--ignore-not-found=true", "--wait=true"],
        check=False,
    )


def read_ca_bundle() -> str:
    with urllib.request.urlopen(RDS_CA_URL, timeout=30) as response:
        bundle = response.read()
    digest = hashlib.sha256(bundle).hexdigest()
    if digest != RDS_CA_SHA256:
        raise BootstrapError("AWS RDS CA bundle digest does not match the reviewed pin")
    return bundle.decode("ascii")


def load_inputs(terraform_root: Path) -> dict[str, object]:
    database = terraform_output(terraform_root, "web3signer_database")
    secret_arns = terraform_output(terraform_root, "web3signer_secret_arns")
    expected_group = str(terraform_output(terraform_root, "web3signer_migration_pod_security_group_id"))
    cluster_name = str(terraform_output(terraform_root, "cluster_name"))

    if not isinstance(database, dict) or not isinstance(secret_arns, dict):
        raise BootstrapError("Terraform signer outputs have an unexpected shape")
    required_database = {"address", "port", "database", "vpc_cidr", "master_secret_arn"}
    if not required_database.issubset(database):
        raise BootstrapError("Terraform database output is incomplete")
    if not str(expected_group).startswith("sg-"):
        raise BootstrapError("migration Pod security-group output is invalid")
    ipaddress.ip_network(str(database["vpc_cidr"]), strict=True)

    current_context = run(["kubectl", "config", "current-context"]).stdout.strip()
    if cluster_name not in current_context:
        raise BootstrapError("kubectl context does not name the Terraform EKS cluster")

    return {
        "database": database,
        "target_secret": secret_arns["database_connection"],
        "expected_group": expected_group,
    }


def ensure_empty_target(secret_id: str) -> None:
    result = run(
        [
            "aws",
            "secretsmanager",
            "list-secret-version-ids",
            "--secret-id",
            secret_id,
            "--query",
            "length(Versions)",
            "--output",
            "text",
        ]
    )
    if result.stdout.strip() != "0":
        raise BootstrapError("database application secret already has a version; refusing implicit rotation")


def load_master_secret(secret_id: str, database: dict[str, object]) -> dict[str, str]:
    result = run(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_id,
            "--query",
            "SecretString",
            "--output",
            "text",
        ]
    )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise BootstrapError("RDS master secret has an unexpected shape")
    if value.get("username") != "web3signer_admin":
        raise BootstrapError("RDS master username does not match the Terraform contract")
    # An RDS-managed master secret is a credential boundary, not the database
    # connection contract. The observed AWS-managed JSON contains only
    # ``username`` and ``password``; the current endpoint and port come from
    # Terraform's applied DB-instance outputs. If AWS later adds routing fields,
    # still reject a disagreement rather than silently preferring either source.
    if "host" in value and value["host"] != database["address"]:
        raise BootstrapError("RDS master-secret host does not match Terraform")
    if "port" in value:
        try:
            observed_port = int(value["port"])
        except (TypeError, ValueError) as error:
            raise BootstrapError("RDS master-secret port is invalid") from error
        if observed_port != int(database["port"]):
            raise BootstrapError("RDS master-secret port does not match Terraform")
    password = value.get("password")
    if not isinstance(password, str) or len(password) < 20:
        raise BootstrapError("RDS master password is absent or unexpectedly short")
    return {"username": value["username"], "password": password}


def ensure_bootstrap_boundary(expected_group: str) -> None:
    for kind, name in (
        ("job", RESOURCE_NAME),
        ("networkpolicy", RESOURCE_NAME),
        ("secret", RESOURCE_NAME),
        ("configmap", CA_CONFIGMAP),
    ):
        if resource_exists(kind, name):
            raise BootstrapError(f"stale {kind}/{name} exists; inspect and remove it before retrying")

    policy = kubectl_json(
        ["-n", NAMESPACE, "get", "securitygrouppolicy", "web3signer-schema"]
    )
    observed_groups = policy.get("spec", {}).get("securityGroups", {}).get("groupIds", [])
    if observed_groups != [expected_group]:
        raise BootstrapError("applied migration SecurityGroupPolicy does not match Terraform")


def apply_bootstrap_resources(
    *,
    database: dict[str, object],
    master: dict[str, str],
    app_password: str,
    ca_bundle: str,
) -> None:
    apply_object(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": CA_CONFIGMAP, "namespace": NAMESPACE},
            "data": {"global-bundle.pem": ca_bundle},
        }
    )
    apply_object(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": RESOURCE_NAME,
                "namespace": NAMESPACE,
                "labels": {
                    "app.kubernetes.io/part-of": "ethereum-validator-platform",
                    "platform.galaxy-lab/qualification": "database-bootstrap",
                },
            },
            "type": "Opaque",
            "stringData": {
                "host": str(database["address"]),
                "port": str(database["port"]),
                "database": str(database["database"]),
                "masterUsername": master["username"],
                "masterPassword": master["password"],
                "appUsername": APP_USERNAME,
                "appPassword": app_password,
            },
        }
    )

    rendered = FIXTURE.read_text(encoding="utf-8").replace(
        "${WEB3SIGNER_DATABASE_VPC_CIDR}", str(database["vpc_cidr"])
    )
    if "${" in rendered:
        raise BootstrapError("bootstrap fixture contains an unresolved substitution")
    run(["kubectl", "apply", "-f", "-"], input_text=rendered)


def wait_and_verify_job(expected_group: str) -> None:
    wait = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "wait",
            "--for=condition=Complete",
            f"job/{RESOURCE_NAME}",
            "--timeout=5m",
        ],
        check=False,
    )
    if wait.returncode != 0:
        raise BootstrapError("database bootstrap Job did not complete; inspect its status before retrying")

    pods = kubectl_json(
        ["-n", NAMESPACE, "get", "pods", "-l", f"job-name={RESOURCE_NAME}"]
    )
    items = pods.get("items", []) if isinstance(pods, dict) else []
    if len(items) != 1:
        raise BootstrapError("expected exactly one database bootstrap Pod")
    annotations = items[0].get("metadata", {}).get("annotations", {})
    eni_value = annotations.get("vpc.amazonaws.com/pod-eni")
    if not eni_value:
        raise BootstrapError("database bootstrap Pod has no branch-ENI annotation")
    eni_data = json.loads(eni_value)
    eni_id = eni_data.get("eniId") or eni_data.get("eniID")
    if not isinstance(eni_id, str) or not eni_id.startswith("eni-"):
        raise BootstrapError("database bootstrap Pod branch-ENI annotation is invalid")

    response = aws_json(
        ["ec2", "describe-network-interfaces", "--network-interface-ids", eni_id]
    )
    interfaces = response.get("NetworkInterfaces", []) if isinstance(response, dict) else []
    groups = {
        item["GroupId"]
        for interface in interfaces
        for item in interface.get("Groups", [])
        if "GroupId" in item
    }
    if expected_group not in groups:
        raise BootstrapError("database bootstrap Pod branch ENI lacks the Terraform migration group")


def put_and_verify_application_secret(
    secret_id: str,
    database: dict[str, object],
    app_password: str,
) -> None:
    secret_value = {
        "host": str(database["address"]),
        "port": str(database["port"]),
        "database": str(database["database"]),
        "username": APP_USERNAME,
        "password": app_password,
    }
    aws_json(
        ["secretsmanager", "put-secret-value", "--cli-input-json", "file:///dev/stdin"],
        input_object={"SecretId": secret_id, "SecretString": json.dumps(secret_value, separators=(",", ":"))},
    )
    result = run(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_id,
            "--query",
            "SecretString",
            "--output",
            "text",
        ]
    )
    observed = json.loads(result.stdout)
    if observed != secret_value:
        raise BootstrapError("database application secret readback did not match the in-memory payload")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--terraform-root",
        type=Path,
        default=ROOT / "terraform" / "environments" / "dev",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_commands()
    inputs = load_inputs(args.terraform_root.resolve())
    database = inputs["database"]
    assert isinstance(database, dict)
    target_secret = str(inputs["target_secret"])
    ensure_bootstrap_boundary(str(inputs["expected_group"]))
    ensure_empty_target(target_secret)
    master = load_master_secret(str(database["master_secret_arn"]), database)
    ca_bundle = read_ca_bundle()
    app_password = secrets.token_urlsafe(48)

    try:
        apply_bootstrap_resources(
            database=database,
            master=master,
            app_password=app_password,
            ca_bundle=ca_bundle,
        )
        wait_and_verify_job(str(inputs["expected_group"]))
        put_and_verify_application_secret(target_secret, database, app_password)
    finally:
        cleanup()

    print(
        "Created the restricted Web3Signer database role, verified its migration branch ENI, "
        "stored the application credential, and removed temporary Kubernetes material."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BootstrapError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"database bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(1)
