#!/usr/bin/env python3
"""Bind a Trivy JSON report to the exact image subject requested by CI."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PINNED_IMAGE_RE = re.compile(
    r"^(?P<repository>[A-Za-z0-9][A-Za-z0-9._/:\-]*?)"
    r"@(?P<digest>sha256:[0-9a-f]{64})$"
)
IMAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class EvidenceError(RuntimeError):
    """Scanner output cannot be proven to describe the intended image digest."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"cannot read JSON evidence {path}: {error}") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"JSON evidence {path} must contain an object")
    return value


def verify_scan_evidence(
    expected_image: str,
    report: dict[str, Any],
    scanner_version: dict[str, Any],
) -> dict[str, Any]:
    expected = PINNED_IMAGE_RE.fullmatch(expected_image)
    if expected is None:
        raise EvidenceError(
            f"expected image is not pinned by an exact sha256 digest: {expected_image!r}"
        )

    if report.get("ArtifactType") != "container_image":
        raise EvidenceError(
            "Trivy report ArtifactType must be 'container_image', got "
            f"{report.get('ArtifactType')!r}"
        )
    if report.get("ArtifactName") != expected_image:
        raise EvidenceError(
            "Trivy ArtifactName does not match the requested image: "
            f"expected {expected_image!r}, got {report.get('ArtifactName')!r}"
        )

    metadata = report.get("Metadata")
    if not isinstance(metadata, dict):
        raise EvidenceError("Trivy report Metadata must be an object")
    repository_digests = metadata.get("RepoDigests")
    if not isinstance(repository_digests, list) or not all(
        isinstance(value, str) for value in repository_digests
    ):
        raise EvidenceError("Trivy Metadata.RepoDigests must be a string array")
    expected_digest = expected.group("digest")
    if not any(
        repository_digest.rpartition("@")[2] == expected_digest
        for repository_digest in repository_digests
    ):
        raise EvidenceError(
            "Trivy Metadata.RepoDigests does not contain the requested digest "
            f"{expected_digest}"
        )

    report_trivy = report.get("Trivy")
    if not isinstance(report_trivy, dict) or not isinstance(
        report_trivy.get("Version"), str
    ):
        raise EvidenceError("Trivy report must identify the scanner version")
    if scanner_version.get("Version") != report_trivy["Version"]:
        raise EvidenceError(
            "scanner version evidence does not match the report: "
            f"{scanner_version.get('Version')!r} != {report_trivy['Version']!r}"
        )
    if not isinstance(scanner_version.get("VulnerabilityDB"), dict):
        raise EvidenceError("scanner version evidence must include VulnerabilityDB")
    if not isinstance(report.get("CreatedAt"), str):
        raise EvidenceError("Trivy report must include CreatedAt")

    return {
        "artifactName": report["ArtifactName"],
        "artifactType": report["ArtifactType"],
        "digest": expected_digest,
        "reportCreatedAt": report["CreatedAt"],
        "repositoryDigests": repository_digests,
        "scannerVersion": report_trivy["Version"],
        "vulnerabilityDatabase": scanner_version["VulnerabilityDB"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--scanner-version", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--checkout-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--event", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if IMAGE_ID_RE.fullmatch(args.image_id) is None:
            raise EvidenceError(f"invalid image evidence id: {args.image_id!r}")
        report = load_json(args.report)
        scanner_version = load_json(args.scanner_version)
        verified = verify_scan_evidence(
            args.expected_image,
            report,
            scanner_version,
        )
        subject = {
            "schemaVersion": 1,
            "id": args.image_id,
            "image": args.expected_image,
            "verifiedReport": verified,
            "provenance": {
                "sourceSha": args.source_sha,
                "checkoutSha": args.checkout_sha,
                "workflowRunId": args.run_id,
                "workflowRunAttempt": args.run_attempt,
                "event": args.event,
            },
        }
        args.output.write_text(
            json.dumps(subject, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (EvidenceError, OSError, TypeError, ValueError) as error:
        print(f"Image scan evidence verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
