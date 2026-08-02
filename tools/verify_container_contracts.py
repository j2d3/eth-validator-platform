#!/usr/bin/env python3
"""Verify runtime contracts for digest-pinned container images."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")


class IdentityMode(Enum):
    """How a Kubernetes workload establishes its numeric runtime identity."""

    IMAGE_DEFAULT = "image-default"
    KUBERNETES_OVERRIDE = "kubernetes-override"


@dataclass(frozen=True)
class ContainerContract:
    """A container image and the Kubernetes security contract around it."""

    manifest_path: Path
    workload_kind: str
    container_name: str
    container_group: str = "containers"
    identity_mode: IdentityMode = IdentityMode.IMAGE_DEFAULT
    smoke_entrypoint: str | None = None
    smoke_args: tuple[str, ...] = ()
    smoke_use_declared_args: bool = False
    smoke_tmpfs: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelmImageContract:
    """A digest and security context declared in HelmRelease values."""

    manifest_path: Path
    container_name: str
    image_repository: str
    digest_path: tuple[str, ...]
    pod_security_path: tuple[str, ...]
    container_security_path: tuple[str, ...]
    identity_mode: IdentityMode = IdentityMode.IMAGE_DEFAULT
    identity_probe_entrypoint: str | None = "/usr/bin/id"
    smoke_args: tuple[str, ...] = ()
    smoke_tmpfs: tuple[str, ...] = ()


CONTRACTS = (
    ContainerContract(
        manifest_path=Path("platform/apps/base/web3signer/deployment.yaml"),
        workload_kind="Deployment",
        container_name="web3signer",
    ),
    ContainerContract(
        manifest_path=Path(
            "platform/apps/prerequisites/local/web3signer-schema-job.yaml"
        ),
        workload_kind="Job",
        container_name="copy-web3signer-migrations",
        container_group="initContainers",
        smoke_entrypoint="/bin/cp",
        smoke_use_declared_args=True,
        smoke_tmpfs=(
            # Match Kubelet's root-owned, fsGroup-writable emptyDir mount point.
            "/work/migrations:rw,noexec,nosuid,nodev,size=8m,uid=0,gid=999,mode=3777",
        ),
    ),
    ContainerContract(
        manifest_path=Path(
            "platform/apps/prerequisites/local/web3signer-schema-job.yaml"
        ),
        workload_kind="Job",
        container_name="flyway",
        identity_mode=IdentityMode.KUBERNETES_OVERRIDE,
        smoke_args=("-v",),
        smoke_tmpfs=("/tmp:rw,noexec,nosuid,nodev,size=64m,uid=999,gid=999",),
    ),
)


HELM_IMAGE_CONTRACTS = (
    HelmImageContract(
        manifest_path=Path(
            "platform/infrastructure/controllers/logging-loki.yaml"
        ),
        container_name="loki",
        image_repository="docker.io/grafana/loki",
        digest_path=("spec", "values", "loki", "image", "digest"),
        pod_security_path=(
            "spec",
            "values",
            "loki",
            "podSecurityContext",
        ),
        container_security_path=(
            "spec",
            "values",
            "loki",
            "containerSecurityContext",
        ),
        identity_mode=IdentityMode.KUBERNETES_OVERRIDE,
        identity_probe_entrypoint=None,
        smoke_args=("-version",),
    ),
    HelmImageContract(
        manifest_path=Path(
            "platform/infrastructure/controllers/logging-loki.yaml"
        ),
        container_name="loki-canary",
        image_repository="docker.io/grafana/loki-canary",
        digest_path=("spec", "values", "lokiCanary", "image", "digest"),
        pod_security_path=(
            "spec",
            "values",
            "loki",
            "podSecurityContext",
        ),
        container_security_path=(
            "spec",
            "values",
            "loki",
            "containerSecurityContext",
        ),
        identity_mode=IdentityMode.KUBERNETES_OVERRIDE,
        identity_probe_entrypoint=None,
        smoke_args=("-version",),
    ),
    HelmImageContract(
        manifest_path=Path(
            "platform/infrastructure/controllers/logging-alloy.yaml"
        ),
        container_name="alloy",
        image_repository="docker.io/grafana/alloy",
        digest_path=("spec", "values", "image", "digest"),
        pod_security_path=(
            "spec",
            "values",
            "global",
            "podSecurityContext",
        ),
        container_security_path=(
            "spec",
            "values",
            "alloy",
            "securityContext",
        ),
        identity_mode=IdentityMode.KUBERNETES_OVERRIDE,
        smoke_args=("--version",),
        smoke_tmpfs=("/tmp:rw,noexec,nosuid,nodev,size=16m,uid=473,gid=473",),
    ),
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


def load_workload(path: Path, kind: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        documents = tuple(yaml.safe_load_all(source))
    workloads = [
        document for document in documents if document and document.get("kind") == kind
    ]
    if len(workloads) != 1:
        raise ContractError(f"Expected exactly one {kind} in {path}")
    return workloads[0]


def nested_value(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = document
    for key in path:
        value = value[key]
    return value


def required_non_root_id(value: Any, field: str, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ContractError(f"{path}: {field} must be an explicit positive integer")
    return value


def require_hardened_security_context(
    path: Path,
    container_name: str,
    pod_security: dict[str, Any],
    container_security: dict[str, Any],
) -> tuple[int, int]:
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
    if container_security.get("allowPrivilegeEscalation") is not False:
        raise ContractError(
            f"{path}: {container_name} must disable privilege escalation"
        )
    if container_security.get("readOnlyRootFilesystem") is not True:
        raise ContractError(
            f"{path}: {container_name} must use a read-only root filesystem"
        )
    dropped_capabilities = container_security.get("capabilities", {}).get("drop", [])
    if "ALL" not in dropped_capabilities:
        raise ContractError(f"{path}: {container_name} must drop all capabilities")
    seccomp_profile = container_security.get(
        "seccompProfile", pod_security.get("seccompProfile", {})
    )
    if seccomp_profile.get("type") != "RuntimeDefault":
        raise ContractError(
            f"{path}: {container_name} must use the RuntimeDefault seccomp profile"
        )
    return expected_uid, expected_gid


def docker_security_options(expected_uid: int, expected_gid: int) -> list[str]:
    return [
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={expected_uid}:{expected_gid}",
    ]


def verify_contract(contract: ContainerContract, pulled_images: set[str]) -> None:
    path = REPOSITORY_ROOT / contract.manifest_path
    workload = load_workload(path, contract.workload_kind)
    pod_spec = workload["spec"]["template"]["spec"]
    pod_security = pod_spec.get("securityContext", {})
    containers = [
        container
        for container in pod_spec.get(contract.container_group, [])
        if container.get("name") == contract.container_name
    ]
    if len(containers) != 1:
        raise ContractError(
            f"{path}: expected exactly one {contract.container_group} entry named "
            f"{contract.container_name}"
        )

    container = containers[0]
    expected_uid, expected_gid = require_hardened_security_context(
        path,
        contract.container_name,
        pod_security,
        container.get("securityContext", {}),
    )
    image = container.get("image", "")
    if not IMAGE_DIGEST_PATTERN.search(image):
        raise ContractError(
            f"{path}: {contract.container_name} image must be pinned by sha256 digest"
        )

    if image not in pulled_images:
        run(["docker", "pull", image])
        pulled_images.add(image)

    identity_options: list[str] = []
    if contract.identity_mode is IdentityMode.KUBERNETES_OVERRIDE:
        identity_options = [f"--user={expected_uid}:{expected_gid}"]
    identity_probe = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        *identity_options,
        "--entrypoint=/usr/bin/id",
        image,
    ]
    actual_uid = int(run([*identity_probe, "-u"]))
    actual_gid = int(run([*identity_probe, "-g"]))

    if (actual_uid, actual_gid) != (expected_uid, expected_gid):
        identity_description = (
            "image runtime identity"
            if contract.identity_mode is IdentityMode.IMAGE_DEFAULT
            else "declared Kubernetes identity override"
        )
        raise ContractError(
            f"{path}: declared UID/GID {expected_uid}:{expected_gid} does not match "
            f"{image} {identity_description} {actual_uid}:{actual_gid}"
        )

    smoke_args = (
        tuple(container.get("args", ()))
        if contract.smoke_use_declared_args
        else contract.smoke_args
    )
    if contract.smoke_entrypoint or smoke_args:
        smoke_probe = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            *docker_security_options(expected_uid, expected_gid),
        ]
        for tmpfs in contract.smoke_tmpfs:
            smoke_probe.extend(("--tmpfs", tmpfs))
        if contract.smoke_entrypoint:
            smoke_probe.append(f"--entrypoint={contract.smoke_entrypoint}")
        smoke_probe.extend((image, *smoke_args))
        run(smoke_probe)

    identity_description = (
        "native image identity"
        if contract.identity_mode is IdentityMode.IMAGE_DEFAULT
        else "Kubernetes identity override"
    )
    print(
        f"Verified {contract.container_name}: {image} accepts hardened "
        f"UID/GID {actual_uid}:{actual_gid} via {identity_description}."
    )


def verify_helm_image_contract(
    contract: HelmImageContract, pulled_images: set[str]
) -> None:
    path = REPOSITORY_ROOT / contract.manifest_path
    with path.open(encoding="utf-8") as source:
        release = yaml.safe_load(source)

    pod_security = nested_value(release, contract.pod_security_path)
    container_security = nested_value(release, contract.container_security_path)
    expected_uid, expected_gid = require_hardened_security_context(
        path,
        contract.container_name,
        pod_security,
        container_security,
    )
    digest = nested_value(release, contract.digest_path)
    image = f"{contract.image_repository}@{digest}"
    if not IMAGE_DIGEST_PATTERN.search(image):
        raise ContractError(
            f"{path}: {contract.container_name} image must be pinned by sha256 digest"
        )

    if image not in pulled_images:
        run(["docker", "pull", image])
        pulled_images.add(image)

    actual_uid = expected_uid
    actual_gid = expected_gid
    if contract.identity_probe_entrypoint:
        identity_options: list[str] = []
        if contract.identity_mode is IdentityMode.KUBERNETES_OVERRIDE:
            identity_options = [f"--user={expected_uid}:{expected_gid}"]
        identity_probe = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            *identity_options,
            f"--entrypoint={contract.identity_probe_entrypoint}",
            image,
        ]
        actual_uid = int(run([*identity_probe, "-u"]))
        actual_gid = int(run([*identity_probe, "-g"]))
        if (actual_uid, actual_gid) != (expected_uid, expected_gid):
            raise ContractError(
                f"{path}: declared UID/GID {expected_uid}:{expected_gid} does not "
                f"match {image} runtime identity {actual_uid}:{actual_gid}"
            )

    if contract.smoke_args:
        smoke_probe = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            *docker_security_options(expected_uid, expected_gid),
        ]
        for tmpfs in contract.smoke_tmpfs:
            smoke_probe.extend(("--tmpfs", tmpfs))
        smoke_probe.extend((image, *contract.smoke_args))
        run(smoke_probe)

    identity_description = (
        "native image identity"
        if contract.identity_mode is IdentityMode.IMAGE_DEFAULT
        else "Kubernetes identity override"
    )
    print(
        f"Verified {contract.container_name}: {image} accepts hardened "
        f"UID/GID {actual_uid}:{actual_gid} via {identity_description}."
    )


def main() -> int:
    try:
        pulled_images: set[str] = set()
        for contract in CONTRACTS:
            verify_contract(contract, pulled_images)
        for contract in HELM_IMAGE_CONTRACTS:
            verify_helm_image_contract(contract, pulled_images)
    except (ContractError, KeyError, TypeError, ValueError) as error:
        print(f"Container contract validation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
