#!/usr/bin/env python3
"""Discover container-image identities and coverage gaps from desired state.

The repository has two materially different image classes:

* exact digest references that can be scanned reproducibly; and
* tag/default-chart references whose artifact identity is not yet committed.

This tool reports both. It never turns an unpinned image into scan evidence by
resolving a mutable tag at workflow runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_container_contracts import (  # noqa: E402
    HELM_IMAGE_CONTRACTS,
    nested_value,
)


PINNED_IMAGE_RE = re.compile(
    r"^(?P<repository>[A-Za-z0-9][A-Za-z0-9._/:\-]*?)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)
IMAGE_VALUE_KEYS = {"image", "imageName", "initImage", "containerImage", "imageRef"}
YAML_SUFFIXES = {".yaml", ".yml"}
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".local",
    ".claude",
    ".terraform",
    "node_modules",
    "secrets",
}
SCOPE_EXCLUSIONS = (
    (
        "charts/**/templates/*.yaml",
        "Go-templated image expressions are resolved from in-scope values*.yaml files; "
        "literal template image lines are still scanned lexically",
    ),
    (
        "docs/** and tests/**",
        "narrative references and test fixtures are not deployable image sources",
    ),
    (
        ".git/**, .local/**, .claude/**, .terraform/**, node_modules/**, and secrets/**",
        "repository metadata, generated tools, agent state, downloaded provider/module "
        "caches, dependencies, and secret material are never desired-state inputs",
    ),
)
PINNED_IMAGE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9._/:\-])"
    r"(?P<image>[A-Za-z0-9][A-Za-z0-9._/:\-]*?"
    r"@sha256:[0-9a-f]{64})"
    r"(?![0-9a-f])"
)
SHELL_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:(?:readonly|export|local)\s+)*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)="
    r"(?P<quote>['\"]?)(?P<value>[^'\"\s]+)(?P=quote)"
)
DOCKERFILE_FROM_RE = re.compile(
    r"^\s*FROM\s+(?:(?:--platform=\S+)\s+)?(?P<image>\S+)", re.IGNORECASE
)
HELM_TEMPLATE_IMAGE_RE = re.compile(r"^\s*image:\s*(?P<image>\S.*?)\s*$")


class InventoryError(RuntimeError):
    """Desired state cannot be converted into truthful image evidence."""


@dataclass(frozen=True)
class ImageRecord:
    id: str
    image: str
    repository: str
    digest: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class CoverageGap:
    kind: str
    subject: str
    source: str
    reason: str


@dataclass(frozen=True)
class Inventory:
    images: tuple[ImageRecord, ...]
    gaps: tuple[CoverageGap, ...]
    scope_exclusions: tuple[tuple[str, str], ...]


def is_excluded_path(relative_path: Path) -> bool:
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative_path.parts):
        return True
    if relative_path.parts and relative_path.parts[0] in {"docs", "tests"}:
        return True
    return (
        len(relative_path.parts) >= 3
        and relative_path.parts[0] == "charts"
        and "templates" in relative_path.parts
    )


def repository_paths(predicate: Callable[[Path], bool]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for directory, child_directories, filenames in os.walk(ROOT):
        relative_directory = Path(directory).relative_to(ROOT)
        child_directories[:] = [
            child
            for child in child_directories
            if child not in EXCLUDED_DIRECTORY_NAMES
            and not (relative_directory == Path(".") and child in {"docs", "tests"})
            and not (
                relative_directory.parts
                and relative_directory.parts[0] == "charts"
                and child == "templates"
            )
        ]
        for filename in filenames:
            relative_path = relative_directory / filename
            if is_excluded_path(relative_path):
                continue
            if predicate(relative_path):
                paths.append(relative_path)
    return tuple(sorted(paths))


def yaml_paths() -> tuple[Path, ...]:
    """Return every parseable runtime/configuration YAML source in the repository."""

    return repository_paths(lambda path: path.suffix.lower() in YAML_SUFFIXES)


def shell_paths() -> tuple[Path, ...]:
    """Return every shell source that may declare a runtime image constant."""

    return repository_paths(lambda path: path.suffix.lower() == ".sh")


def dockerfile_paths() -> tuple[Path, ...]:
    """Return every first-party image build definition."""

    return repository_paths(lambda path: "dockerfile" in path.name.lower())


def helm_template_paths() -> tuple[Path, ...]:
    """Return templated Helm YAML for lexical literal-image accounting."""

    paths: list[Path] = []
    for path in (ROOT / "charts").rglob("*.yaml"):
        relative_path = path.relative_to(ROOT)
        if "templates" in relative_path.parts and path.is_file():
            paths.append(relative_path)
    return tuple(sorted(paths))


def walk_image_values(value: Any, location: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in IMAGE_VALUE_KEYS and isinstance(child, str):
                yield child, child_location
            elif key in IMAGE_VALUE_KEYS and isinstance(child, dict):
                repository = child.get("repository")
                registry = child.get("registry")
                if isinstance(repository, str):
                    if isinstance(registry, str) and registry:
                        repository = f"{registry.rstrip('/')}/{repository.lstrip('/')}"
                    digest = child.get("digest")
                    tag = child.get("tag")
                    if isinstance(digest, str) and digest:
                        yield f"{repository}@{digest}", child_location
                    elif isinstance(tag, str) and tag:
                        yield f"{repository}:{tag}", child_location

            if key == "images" and isinstance(child, list):
                for index, replacement in enumerate(child):
                    if not isinstance(replacement, dict):
                        continue
                    repository = replacement.get("newName", replacement.get("name"))
                    if not isinstance(repository, str):
                        continue
                    digest = replacement.get("digest")
                    tag = replacement.get("newTag")
                    replacement_location = f"{child_location}[{index}]"
                    if isinstance(digest, str) and digest:
                        yield f"{repository}@{digest}", replacement_location
                    elif isinstance(tag, str) and tag:
                        yield f"{repository}:{tag}", replacement_location
            yield from walk_image_values(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_image_values(child, f"{location}[{index}]")


def walk_workflow_container_values(
    value: Any, location: str = "$"
) -> Iterable[tuple[str, str]]:
    """Discover the string form of GitHub job containers and Docker actions."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key == "container" and isinstance(child, str):
                yield child, child_location
            if (
                key == "uses"
                and isinstance(child, str)
                and child.startswith("docker://")
            ):
                yield child.removeprefix("docker://"), child_location
            yield from walk_workflow_container_values(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_workflow_container_values(child, f"{location}[{index}]")


def image_id(repository: str, digest: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", repository.lower()).strip("-")[-36:]
    suffix = hashlib.sha256(f"{repository}@sha256:{digest}".encode()).hexdigest()[:10]
    return f"{readable}-{suffix}"


def add_image(discovered: dict[str, set[str]], image: str, source: str) -> None:
    match = PINNED_IMAGE_RE.fullmatch(image)
    if not match:
        raise InventoryError(f"{source}: malformed digest-pinned image {image!r}")
    discovered.setdefault(image, set()).add(source)


def add_image_or_gap(
    discovered: dict[str, set[str]],
    gaps: list[CoverageGap],
    image: str,
    source: str,
) -> None:
    image = image.strip()
    if not image:
        gaps.append(
            CoverageGap(
                kind="dynamic-image",
                subject="<empty>",
                source=source,
                reason="runtime image identity is empty or supplied outside committed source",
            )
        )
    elif "@sha256:" in image:
        add_image(discovered, image, source)
    else:
        kind = "dynamic-image" if "$" in image else "unpinned-image"
        reason = (
            "runtime image identity is assembled dynamically and cannot be durable scan evidence"
            if kind == "dynamic-image"
            else (
                "desired state names a tag rather than an exact sha256 digest; "
                "runtime resolution would not be durable scan evidence"
            )
        )
        gaps.append(
            CoverageGap(
                kind=kind,
                subject=image,
                source=source,
                reason=reason,
            )
        )


def load_documents(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        with path.open(encoding="utf-8") as stream:
            return tuple(
                document
                for document in yaml.safe_load_all(stream)
                if isinstance(document, dict)
            )
    except (OSError, yaml.YAMLError) as error:
        raise InventoryError(
            f"cannot parse {path.relative_to(ROOT)}: {error}"
        ) from error


def discover_source_images() -> tuple[dict[str, set[str]], list[CoverageGap]]:
    discovered: dict[str, set[str]] = {}
    gaps: list[CoverageGap] = []

    for relative_path in yaml_paths():
        path = ROOT / relative_path
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise InventoryError(f"cannot read {relative_path}: {error}") from error
        for line_number, line in enumerate(raw_lines, start=1):
            for match in PINNED_IMAGE_TOKEN_RE.finditer(line):
                add_image(
                    discovered,
                    match.group("image"),
                    f"{relative_path}:{line_number}:yaml-exact-reference",
                )
        for document_index, document in enumerate(load_documents(path)):
            for image, location in walk_image_values(document):
                source = f"{relative_path}:{document_index + 1}:{location}"
                add_image_or_gap(discovered, gaps, image, source)
            if relative_path.parts[:2] == (".github", "workflows"):
                for image, location in walk_workflow_container_values(document):
                    source = f"{relative_path}:{document_index + 1}:{location}"
                    add_image_or_gap(discovered, gaps, image, source)

    for relative_path in shell_paths():
        path = ROOT / relative_path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise InventoryError(f"cannot read {relative_path}: {error}") from error
        for line_number, line in enumerate(lines, start=1):
            source = f"{relative_path}:{line_number}:shell-image-reference"
            for match in PINNED_IMAGE_TOKEN_RE.finditer(line):
                add_image(discovered, match.group("image"), source)
            assignment = SHELL_ASSIGNMENT_RE.match(line)
            if assignment and assignment.group("name").endswith(
                ("_IMAGE", "_IMAGE_REF")
            ):
                add_image_or_gap(
                    discovered,
                    gaps,
                    assignment.group("value"),
                    source,
                )

    for relative_path in dockerfile_paths():
        path = ROOT / relative_path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise InventoryError(f"cannot read {relative_path}: {error}") from error
        for line_number, line in enumerate(lines, start=1):
            match = DOCKERFILE_FROM_RE.match(line)
            if not match or match.group("image").lower() == "scratch":
                continue
            add_image_or_gap(
                discovered,
                gaps,
                match.group("image"),
                f"{relative_path}:{line_number}:dockerfile-base",
            )

    for relative_path in helm_template_paths():
        path = ROOT / relative_path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise InventoryError(f"cannot read {relative_path}: {error}") from error
        for line_number, line in enumerate(lines, start=1):
            match = HELM_TEMPLATE_IMAGE_RE.match(line)
            if not match:
                continue
            image = match.group("image").strip("'\"")
            if image.startswith("{{"):
                if "image" not in image.lower():
                    raise InventoryError(
                        f"{relative_path}:{line_number}: unaccounted Helm image expression "
                        f"{image!r}"
                    )
                continue
            add_image_or_gap(
                discovered,
                gaps,
                image,
                f"{relative_path}:{line_number}:helm-template-literal",
            )

    return discovered, gaps


def discover_helm_contract_images(discovered: dict[str, set[str]]) -> None:
    for contract in HELM_IMAGE_CONTRACTS:
        path = ROOT / contract.manifest_path
        documents = load_documents(path)
        if len(documents) != 1:
            raise InventoryError(
                f"expected one HelmRelease in {contract.manifest_path}"
            )
        digest = nested_value(documents[0], contract.digest_path)
        image = f"{contract.image_repository}@{digest}"
        add_image(
            discovered,
            image,
            f"{contract.manifest_path}:runtime-contract:{contract.container_name}",
        )


def discover_chart_default_gaps() -> list[CoverageGap]:
    gaps: list[CoverageGap] = []
    for relative_path in yaml_paths():
        path = ROOT / relative_path
        for document in load_documents(path):
            if document.get("kind") != "HelmRelease":
                continue
            chart_spec = document.get("spec", {}).get("chart", {}).get("spec", {})
            source_kind = chart_spec.get("sourceRef", {}).get("kind")
            if source_kind not in {"HelmRepository", "OCIRepository"}:
                # First-party GitRepository charts are accounted for through every
                # values file plus lexical checks of their templated image lines.
                continue
            name = document.get("metadata", {}).get("name", "unknown")
            version = chart_spec.get("version", "unknown")
            gaps.append(
                CoverageGap(
                    kind="helm-chart-defaults",
                    subject=f"{name}@{version}",
                    source=str(relative_path),
                    reason=(
                        "the chart version is pinned, but this Phase A inventory does not "
                        "yet render and commit every transitive image digest"
                    ),
                )
            )
    return gaps


def build_inventory() -> Inventory:
    discovered, gaps = discover_source_images()
    discover_helm_contract_images(discovered)
    gaps.extend(discover_chart_default_gaps())

    images: list[ImageRecord] = []
    ids: set[str] = set()
    for image, sources in sorted(discovered.items()):
        match = PINNED_IMAGE_RE.fullmatch(image)
        if match is None:  # guarded by add_image; retained as a type boundary
            raise InventoryError(f"invalid image identity {image!r}")
        repository = match.group("repository")
        digest = match.group("digest")
        record_id = image_id(repository, digest)
        if record_id in ids:
            raise InventoryError(f"generated duplicate image id {record_id}")
        ids.add(record_id)
        images.append(
            ImageRecord(
                id=record_id,
                image=image,
                repository=repository,
                digest=f"sha256:{digest}",
                sources=tuple(sorted(sources)),
            )
        )

    if not images:
        raise InventoryError("no digest-pinned images were discovered")

    unique_gaps = {(gap.kind, gap.subject, gap.source, gap.reason): gap for gap in gaps}
    return Inventory(
        images=tuple(images),
        gaps=tuple(unique_gaps[key] for key in sorted(unique_gaps)),
        scope_exclusions=SCOPE_EXCLUSIONS,
    )


def json_document(inventory: Inventory) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "evidenceSemantics": (
            "Digest identity is proven by Git; scan results are workflow artifacts. "
            "Coverage gaps and missing results are unknown, never zero findings."
        ),
        "images": [asdict(image) for image in inventory.images],
        "coverageGaps": [asdict(gap) for gap in inventory.gaps],
        "scopeExclusions": [
            {"paths": paths, "reason": reason}
            for paths, reason in inventory.scope_exclusions
        ],
    }


def markdown(inventory: Inventory) -> str:
    lines = [
        "## Container image evidence inventory",
        "",
        f"- Exact digest subjects: **{len(inventory.images)}**",
        f"- Explicit coverage gaps: **{len(inventory.gaps)}**",
        "- Semantics: a missing scan is **unknown**, not zero findings.",
        "",
        "| Scan subject | Sources |",
        "|---|---:|",
    ]
    for image in inventory.images:
        lines.append(f"| `{image.image}` | {len(image.sources)} |")
    lines.extend(["", "### Coverage gaps", ""])
    for gap in inventory.gaps:
        lines.append(f"- **{gap.kind}** — `{gap.subject}` ({gap.source}): {gap.reason}")
    lines.extend(["", "### Non-runtime scope exclusions", ""])
    for paths, reason in inventory.scope_exclusions:
        lines.append(f"- `{paths}`: {reason}")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("json", "github-matrix", "markdown"),
        default="json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        inventory = build_inventory()
    except (InventoryError, KeyError, TypeError, ValueError) as error:
        print(f"Image inventory failed: {error}", file=sys.stderr)
        return 1

    if args.format == "github-matrix":
        output = {
            "include": [
                {"id": image.id, "image": image.image} for image in inventory.images
            ]
        }
        print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    elif args.format == "markdown":
        print(markdown(inventory), end="")
    else:
        print(json.dumps(json_document(inventory), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
