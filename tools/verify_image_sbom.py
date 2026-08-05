#!/usr/bin/env python3
"""Bind one CycloneDX SBOM to its verified exact image-scan subject."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SbomError(ValueError):
    """The SBOM cannot be proven to describe the verified scan subject."""


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SbomError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise SbomError(f"{path} must contain a JSON object")
    return value


def parse_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise SbomError("CycloneDX metadata.timestamp must be a string")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SbomError("CycloneDX metadata.timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise SbomError("CycloneDX metadata.timestamp must include a timezone")
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def verified_subject(scan_subject: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    if scan_subject.get("schemaVersion") != 1:
        raise SbomError("scan subject schemaVersion must be 1")
    image = scan_subject.get("image")
    report = scan_subject.get("verifiedReport")
    provenance = scan_subject.get("provenance")
    if not isinstance(report, dict):
        raise SbomError("scan subject verifiedReport must be an object")
    digest = report.get("digest")
    if not isinstance(image, str) or not isinstance(digest, str):
        raise SbomError("scan subject image and verified digest must be strings")
    if DIGEST_RE.fullmatch(digest) is None or not image.endswith(f"@{digest}"):
        raise SbomError("scan subject image and digest are inconsistent")
    if report.get("artifactName") != image:
        raise SbomError("scan subject verified artifact does not match its image")
    if not isinstance(report.get("scannerVersion"), str):
        raise SbomError("scan subject must carry a verified scanner version")
    if not isinstance(provenance, dict):
        raise SbomError("scan subject provenance must be an object")
    return image, digest, report["scannerVersion"], provenance


def verify_sbom(sbom: dict[str, Any], scan_subject: dict[str, Any]) -> dict[str, Any]:
    image, digest, scanner_version, provenance = verified_subject(scan_subject)
    if sbom.get("bomFormat") != "CycloneDX":
        raise SbomError("SBOM bomFormat must be CycloneDX")
    spec_version = sbom.get("specVersion")
    if not isinstance(spec_version, str) or re.fullmatch(r"1\.[0-9]+", spec_version) is None:
        raise SbomError("SBOM specVersion must be a CycloneDX 1.x version")
    if type(sbom.get("version")) is not int or sbom["version"] < 1:
        raise SbomError("SBOM version must be a positive integer")

    metadata = sbom.get("metadata")
    if not isinstance(metadata, dict):
        raise SbomError("SBOM metadata must be an object")
    component = metadata.get("component")
    if not isinstance(component, dict) or component.get("type") != "container":
        raise SbomError("SBOM metadata.component must identify a container")
    if component.get("name") != image:
        raise SbomError("SBOM component name does not match the exact image")
    for field in ("bom-ref", "purl"):
        value = component.get(field)
        if not isinstance(value, str) or f"@{digest}" not in value:
            raise SbomError(f"SBOM component {field} does not bind the exact digest")

    properties = component.get("properties")
    if not isinstance(properties, list) or not all(isinstance(item, dict) for item in properties):
        raise SbomError("SBOM component properties must be an array of objects")
    repository_digests = {
        item.get("value")
        for item in properties
        if item.get("name") == "aquasecurity:trivy:RepoDigest"
        and isinstance(item.get("value"), str)
    }
    if not any(value.endswith(f"@{digest}") for value in repository_digests):
        raise SbomError("SBOM properties do not contain the exact repository digest")

    tools = metadata.get("tools")
    tool_components = tools.get("components") if isinstance(tools, dict) else None
    if not isinstance(tool_components, list) or not any(
        isinstance(tool, dict)
        and tool.get("name") == "trivy"
        and tool.get("version") == scanner_version
        for tool in tool_components
    ):
        raise SbomError("SBOM Trivy identity does not match the verified scan subject")

    components = sbom.get("components")
    if not isinstance(components, list) or not all(
        isinstance(component_record, dict) for component_record in components
    ):
        raise SbomError("SBOM components must be an array of objects")

    return {
        "schemaVersion": 1,
        "image": image,
        "digest": digest,
        "format": "CycloneDX",
        "specVersion": spec_version,
        "generatedAt": parse_timestamp(metadata.get("timestamp")),
        "componentCount": len(components),
        "scannerVersion": scanner_version,
        "provenance": provenance,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--scan-subject", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence = verify_sbom(load_object(args.sbom), load_object(args.scan_subject))
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, SbomError, TypeError, ValueError) as error:
        print(f"SBOM verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
