#!/usr/bin/env python3
"""Verify runtime identity contracts for digest-pinned container images."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")
CONTRACTS = (
    (Path("platform/apps/base/web3signer/deployment.yaml"), "web3signer"),
)


class ContractError(RuntimeError):
    """Raised when a declared container contract is incomplete or incorrect."""


def run(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise ContractError(f"Required command is unavailable: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or "no command output"
        raise ContractError(f"Command failed: {' '.join(command)}\n{detail}") from error
    return result.stdout.strip()


def load_deployment(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        documents = tuple(yaml.safe_load_all(source))
    deployments = [
        document
        for document in documents
        if document and document.get("kind") == "Deployment"
    ]
    if len(deployments) != 1:
        raise ContractError(f"Expected exactly one Deployment in {path}")
    return deployments[0]


def required_non_root_id(value: Any, field: str, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ContractError(f"{path}: {field} must be an explicit positive integer")
    return value


def verify_contract(relative_path: Path, container_name: str) -> None:
    path = REPOSITORY_ROOT / relative_path
    deployment = load_deployment(path)
    pod_spec = deployment["spec"]["template"]["spec"]
    pod_security = pod_spec.get("securityContext", {})
    containers = [
        container
        for container in pod_spec["containers"]
        if container.get("name") == container_name
    ]
    if len(containers) != 1:
        raise ContractError(f"{path}: expected exactly one container named {container_name}")

    container = containers[0]
    container_security = container.get("securityContext", {})
    run_as_non_root = container_security.get(
        "runAsNonRoot", pod_security.get("runAsNonRoot")
    )
    if run_as_non_root is not True:
        raise ContractError(f"{path}: {container_name} must declare runAsNonRoot: true")

    expected_uid = required_non_root_id(
        container_security.get("runAsUser", pod_security.get("runAsUser")),
        "runAsUser",
        path,
    )
    expected_gid = required_non_root_id(
        container_security.get("runAsGroup", pod_security.get("runAsGroup")),
        "runAsGroup",
        path,
    )
    image = container.get("image", "")
    if not IMAGE_DIGEST_PATTERN.search(image):
        raise ContractError(f"{path}: {container_name} image must be pinned by sha256 digest")

    run(["docker", "pull", image])
    probe = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--entrypoint=/usr/bin/id",
        image,
    ]
    actual_uid = int(run([*probe, "-u"]))
    actual_gid = int(run([*probe, "-g"]))

    if (actual_uid, actual_gid) != (expected_uid, expected_gid):
        raise ContractError(
            f"{path}: declared UID/GID {expected_uid}:{expected_gid} does not match "
            f"{image} runtime identity {actual_uid}:{actual_gid}"
        )
    print(
        f"Verified {container_name}: {image} runs as "
        f"UID/GID {actual_uid}:{actual_gid}."
    )


def main() -> int:
    try:
        for relative_path, container_name in CONTRACTS:
            verify_contract(relative_path, container_name)
    except (ContractError, KeyError, TypeError, ValueError) as error:
        print(f"Container contract validation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
