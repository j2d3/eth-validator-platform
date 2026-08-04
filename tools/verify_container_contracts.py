#!/usr/bin/env python3
"""Verify runtime contracts for digest-pinned container images."""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")
MAX_NETWORK_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_NETWORK_ARTIFACT_MEMBER_BYTES = 16 * 1024 * 1024
# Must fit the chart's network-artifacts emptyDir with archive and files together.
MAX_NETWORK_ARTIFACT_WORKING_SET_BYTES = 32 * 1024 * 1024


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
        manifest_path=Path("platform/apps/portal/dev/deployment.yaml"),
        workload_kind="Deployment",
        container_name="status-api",
        identity_mode=IdentityMode.KUBERNETES_OVERRIDE,
        smoke_entrypoint="/usr/local/bin/python",
        smoke_args=("--version",),
    ),
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
        manifest_path=Path("platform/infrastructure/controllers/logging-loki.yaml"),
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
        manifest_path=Path("platform/infrastructure/controllers/logging-loki.yaml"),
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
        manifest_path=Path("platform/infrastructure/controllers/logging-alloy.yaml"),
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


def prometheus_metric_names(metrics: str) -> set[str]:
    """Return sample names from a Prometheus text exposition."""

    return {
        line.split(None, 1)[0].split("{", 1)[0]
        for line in metrics.splitlines()
        if line and not line.startswith("#")
    }


def prometheus_metric_values(metrics: str, metric_name: str) -> list[float]:
    """Return numeric sample values for one Prometheus metric family."""

    values: list[float] = []
    for line in metrics.splitlines():
        if not line or line.startswith("#"):
            continue
        sample, separator, value = line.rpartition(" ")
        if not separator or sample.split("{", 1)[0] != metric_name:
            continue
        try:
            values.append(float(value))
        except ValueError as error:
            raise ContractError(
                f"Metric {metric_name} emitted non-numeric value {value}"
            ) from error
    return values


def verified_ephemery_consensus_artifacts(destination: Path) -> None:
    """Download only the pinned files needed for an offline Lighthouse start."""

    profile_path = REPOSITORY_ROOT / "applications" / "networks" / "ephemery-162.yaml"
    with profile_path.open(encoding="utf-8") as source:
        profile = yaml.safe_load(source)
    artifact = profile["spec"]["artifactBundle"]
    request = urllib.request.Request(
        artifact["url"],
        headers={"User-Agent": "eth-validator-platform-container-contracts"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            archive = response.read(MAX_NETWORK_ARTIFACT_BYTES + 1)
    except OSError as error:
        raise ContractError(
            f"Unable to retrieve pinned network artifact {artifact['url']}: {error}"
        ) from error
    if len(archive) > MAX_NETWORK_ARTIFACT_BYTES:
        raise ContractError(
            f"Pinned network artifact exceeds {MAX_NETWORK_ARTIFACT_BYTES} bytes"
        )
    actual_digest = hashlib.sha256(archive).hexdigest()
    if actual_digest != artifact["sha256"]:
        raise ContractError(
            "Pinned network artifact digest mismatch: "
            f"expected {artifact['sha256']}, got {actual_digest}"
        )

    required_files = set(artifact["files"].values())
    for relative_name in required_files:
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            raise ContractError(
                f"Pinned network artifact declares unsafe path {relative_name}"
            )
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members: dict[str, tarfile.TarInfo] = {}
            for member in bundle:
                normalized_name = member.name.removeprefix("./")
                if normalized_name in members:
                    raise ContractError(
                        "Pinned network artifact contains duplicate normalized path "
                        f"{normalized_name}"
                    )
                members[normalized_name] = member
            extracted_bytes = 0
            for relative_name in sorted(required_files):
                member = members.get(relative_name)
                if member is None or not member.isfile():
                    raise ContractError(
                        f"Pinned network artifact is missing {relative_name}"
                    )
                source = bundle.extractfile(member)
                if source is None:
                    raise ContractError(
                        f"Pinned network artifact cannot read {relative_name}"
                    )
                if member.size > MAX_NETWORK_ARTIFACT_MEMBER_BYTES:
                    raise ContractError(
                        f"Pinned network artifact member {relative_name} exceeds "
                        f"{MAX_NETWORK_ARTIFACT_MEMBER_BYTES} bytes"
                    )
                extracted_bytes += member.size
                if (
                    len(archive) + extracted_bytes
                    > MAX_NETWORK_ARTIFACT_WORKING_SET_BYTES
                ):
                    raise ContractError(
                        "Pinned network artifact archive and selected members exceed "
                        f"the {MAX_NETWORK_ARTIFACT_WORKING_SET_BYTES}-byte chart "
                        "volume"
                    )
                payload = source.read(MAX_NETWORK_ARTIFACT_MEMBER_BYTES + 1)
                if len(payload) != member.size:
                    raise ContractError(
                        f"Pinned network artifact member {relative_name} size mismatch"
                    )
                target = destination / relative_name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                target.chmod(0o444)
        destination.chmod(0o555)
    except (tarfile.TarError, OSError) as error:
        raise ContractError(
            f"Unable to read pinned network artifact: {error}"
        ) from error


def lighthouse_metrics(
    lighthouse_image: str,
    probe_image: str,
    required_metrics: set[str],
) -> str:
    """Connect two exact Lighthouse processes in one isolated network namespace."""

    with tempfile.TemporaryDirectory(prefix="lighthouse-network-") as directory:
        network_directory = Path(directory)
        verified_ephemery_consensus_artifacts(network_directory)
        common_client_args = [
            "bn",
            "--testnet-dir=/network",
            "--datadir=/data",
            "--execution-endpoint=http://127.0.0.1:8551",
            "--execution-jwt-secret-key=0001020304050607080900010203040506070809000102030405060708090001",
            "--disable-enr-auto-update",
            "--disable-upnp",
            "--target-peers=1",
        ]
        container_id = run(
            [
                "docker",
                "create",
                "--pull=never",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=1000:1000",
                "--tmpfs=/data:rw,noexec,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=0700",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m,uid=1000,gid=1000,mode=0700",
                f"--mount=type=bind,src={network_directory},dst=/network,readonly",
                "--entrypoint=lighthouse",
                lighthouse_image,
                *common_client_args,
                "--metrics",
                "--metrics-address=127.0.0.1",
                "--metrics-port=8008",
                "--listen-address=127.0.0.1",
            ]
        )
        peer_container_id: str | None = None

        def container_logs(target: str) -> str:
            result = subprocess.run(
                ["docker", "logs", target],
                check=False,
                capture_output=True,
                text=True,
            )
            return (result.stdout + result.stderr).strip()

        try:
            run(["docker", "start", container_id])
            peer_id: str | None = None
            for _ in range(30):
                match = re.search(
                    r"peer_id:\s+([A-Za-z0-9]+)", container_logs(container_id)
                )
                if match:
                    peer_id = match.group(1)
                    break
                time.sleep(1)
            if peer_id is None:
                raise ContractError(
                    f"{lighthouse_image} did not publish a peer ID:\n"
                    f"{container_logs(container_id)}"
                )
            peer_container_id = run(
                [
                    "docker",
                    "create",
                    "--pull=never",
                    f"--network=container:{container_id}",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--user=1000:1000",
                    "--tmpfs=/data:rw,noexec,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=0700",
                    "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m,uid=1000,gid=1000,mode=0700",
                    f"--mount=type=bind,src={network_directory},dst=/network,readonly",
                    "--entrypoint=lighthouse",
                    lighthouse_image,
                    *common_client_args,
                    "--listen-address=127.0.0.1",
                    "--zero-ports",
                    f"--boot-nodes=/ip4/127.0.0.1/tcp/9000/p2p/{peer_id}",
                ]
            )
            run(["docker", "start", peer_container_id])

            last_error = "metrics endpoint was not ready"
            for _ in range(45):
                state = run(
                    ["docker", "inspect", "--format={{.State.Running}}", container_id]
                )
                if state != "true":
                    raise ContractError(
                        f"{lighthouse_image} exited before metrics were ready:\n"
                        f"{container_logs(container_id)}"
                    )
                peer_state = run(
                    [
                        "docker",
                        "inspect",
                        "--format={{.State.Running}}",
                        peer_container_id,
                    ]
                )
                if peer_state != "true":
                    raise ContractError(
                        f"{lighthouse_image} peer exited before metrics were ready:\n"
                        f"{container_logs(peer_container_id)}"
                    )
                try:
                    metrics = run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--pull=never",
                            f"--network=container:{container_id}",
                            "--read-only",
                            "--cap-drop=ALL",
                            "--security-opt=no-new-privileges",
                            "--user=1000:1000",
                            "--entrypoint=wget",
                            probe_image,
                            "-qO-",
                            "http://127.0.0.1:8008/metrics",
                        ]
                    )
                    missing = required_metrics - prometheus_metric_names(metrics)
                    peer_values = prometheus_metric_values(
                        metrics, "sync_peers_per_status"
                    )
                    if not missing and sum(peer_values) >= 1:
                        return metrics
                    last_error = (
                        f"metrics not emitted yet: {sorted(missing)}; "
                        f"sync_peers_per_status={peer_values}"
                    )
                except ContractError as error:
                    last_error = str(error)
                time.sleep(1)
            raise ContractError(
                f"{lighthouse_image} metrics endpoint did not become ready: "
                f"{last_error}\n{container_logs(container_id)}\n"
                f"Peer logs:\n{container_logs(peer_container_id)}"
            )
        finally:
            if peer_container_id is not None:
                subprocess.run(
                    ["docker", "container", "rm", "--force", peer_container_id],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            subprocess.run(
                ["docker", "container", "rm", "--force", container_id],
                check=False,
                capture_output=True,
                text=True,
            )


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


def verify_ethereum_client_contracts(pulled_images: set[str]) -> None:
    """Exercise explicit non-root client identity and exact sync metrics.

    Both upstream images default to root. The restricted EKS namespace therefore
    relies on the chart's numeric 1000:1000 override, not on image metadata. The
    runtime probes make sync recording rules depend on metric names emitted
    by the exact pinned binaries rather than on remembered documentation.
    """

    values_path = REPOSITORY_ROOT / "charts" / "ethereum-node" / "values.yaml"
    with values_path.open(encoding="utf-8") as source:
        values = yaml.safe_load(source)

    geth_image = values["executionClients"]["geth"]["image"]
    lighthouse_image = values["consensusClients"]["lighthouse"]["image"]
    probe_image = values["networkArtifactLoader"]["verifyImage"]
    for image in (geth_image, lighthouse_image, probe_image):
        if not IMAGE_DIGEST_PATTERN.search(image):
            raise ContractError(
                f"{values_path}: client image must be digest pinned: {image}"
            )
        if image not in pulled_images:
            run(["docker", "pull", image])
            pulled_images.add(image)

    common = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        *docker_security_options(1000, 1000),
    ]
    run([*common, "--entrypoint=geth", geth_image, "version"])
    run([*common, "--entrypoint=lighthouse", lighthouse_image, "--version"])

    metrics_script = r"""
set -eu
geth_pid=""
cleanup() {
  if [ -n "$geth_pid" ]; then
    kill "$geth_pid" 2>/dev/null || true
    wait "$geth_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
geth --dev --datadir=/data --datadir.minfreedisk=1 --http --http.addr=127.0.0.1 \
  --metrics --metrics.addr=127.0.0.1 --metrics.port=6060 \
  >/tmp/geth.log 2>&1 &
geth_pid=$!
tries=0
until wget -qO /tmp/metrics \
    http://127.0.0.1:6060/debug/metrics/prometheus 2>/dev/null; do
  tries=$((tries + 1))
  if [ "$tries" -ge 30 ]; then
    cat /tmp/geth.log >&2
    exit 1
  fi
  sleep 1
done
cat /tmp/metrics
""".strip()
    metrics = run(
        [
            *common,
            "--tmpfs=/data:rw,noexec,nosuid,nodev,size=64m,uid=1000,gid=1000,mode=0700",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,uid=1000,gid=1000,mode=0700",
            "--entrypoint=/bin/sh",
            geth_image,
            "-ec",
            metrics_script,
        ]
    )
    geth_required_metrics = {"chain_head_block", "chain_head_header", "p2p_peers"}
    missing = geth_required_metrics - prometheus_metric_names(metrics)
    if missing:
        raise ContractError(
            f"{geth_image} did not emit required sync metrics: {sorted(missing)}"
        )

    lighthouse_required_metrics = {
        "beacon_head_state_finalized_epoch",
        "beacon_head_state_slot",
        "sync_peers_per_status",
        "slotclock_present_epoch",
        "slotclock_present_slot",
    }
    missing = lighthouse_required_metrics - prometheus_metric_names(
        lighthouse_metrics(
            lighthouse_image,
            probe_image,
            lighthouse_required_metrics,
        )
    )
    if missing:
        raise ContractError(
            f"{lighthouse_image} did not emit required sync metrics: {sorted(missing)}"
        )

    print(
        "Verified Geth and Lighthouse run as hardened UID/GID 1000:1000; "
        f"Geth emitted {', '.join(sorted(geth_required_metrics))}; "
        "Lighthouse emitted "
        f"{', '.join(sorted(lighthouse_required_metrics))}."
    )


def main() -> int:
    try:
        pulled_images: set[str] = set()
        for contract in CONTRACTS:
            verify_contract(contract, pulled_images)
        for contract in HELM_IMAGE_CONTRACTS:
            verify_helm_image_contract(contract, pulled_images)
        verify_ethereum_client_contracts(pulled_images)
    except (ContractError, KeyError, TypeError, ValueError) as error:
        print(f"Container contract validation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
