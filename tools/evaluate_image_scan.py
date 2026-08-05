#!/usr/bin/env python3
"""Evaluate verified Trivy evidence without making a promotion decision."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VULNERABILITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
OWNER_RE = re.compile(r"^@?[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TRIVY_TIMESTAMP_RE = re.compile(
    r"^(?P<seconds>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?Z$"
)
COUNTED_SEVERITIES = ("CRITICAL", "HIGH")


class EvaluationError(RuntimeError):
    """The evidence or its exception contract is invalid."""


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"cannot read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise EvaluationError(f"JSON document {path} must contain an object")
    return value


def parse_utc_timestamp(value: Any, *, field: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise EvaluationError(f"{field} must be a second-precision UTC timestamp")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as error:
        raise EvaluationError(
            f"{field} is not a valid UTC timestamp: {value!r}"
        ) from error


def parse_trivy_timestamp(value: Any) -> tuple[dt.datetime, int]:
    if not isinstance(value, str):
        raise EvaluationError("Trivy CreatedAt must be an RFC3339 UTC timestamp")
    matched = TRIVY_TIMESTAMP_RE.fullmatch(value)
    if matched is None:
        raise EvaluationError("Trivy CreatedAt must be an RFC3339 UTC timestamp")
    try:
        seconds = dt.datetime.strptime(
            matched.group("seconds"), "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=dt.timezone.utc)
    except ValueError as error:
        raise EvaluationError(
            f"Trivy CreatedAt is not a valid UTC timestamp: {value!r}"
        ) from error
    fraction = matched.group("fraction") or ""
    nanoseconds = int(fraction.ljust(9, "0")) if fraction else 0
    return seconds, nanoseconds


def verified_subject(
    expected_image: str,
    report: dict[str, Any],
    subject: dict[str, Any],
) -> tuple[str, str]:
    image_name, separator, digest = expected_image.rpartition("@")
    if not separator or not image_name or DIGEST_RE.fullmatch(digest) is None:
        raise EvaluationError("expected image must be pinned by an exact sha256 digest")
    if subject.get("schemaVersion") != 1 or subject.get("image") != expected_image:
        raise EvaluationError("scan subject does not identify the expected image")
    verified = subject.get("verifiedReport")
    if not isinstance(verified, dict) or verified.get("digest") != digest:
        raise EvaluationError("scan subject does not verify the expected image digest")
    if report.get("ArtifactType") != "container_image":
        raise EvaluationError("Trivy report is not container-image evidence")
    if report.get("ArtifactName") != expected_image:
        raise EvaluationError("Trivy report does not identify the expected image")
    created_at = report.get("CreatedAt")
    parse_trivy_timestamp(created_at)
    if verified.get("reportCreatedAt") != created_at:
        raise EvaluationError("scan subject and Trivy report timestamps do not match")
    return digest, created_at


def high_critical_findings(report: dict[str, Any]) -> list[dict[str, str]]:
    results = report.get("Results", [])
    if not isinstance(results, list):
        raise EvaluationError("Trivy Results must be an array")
    findings: list[dict[str, str]] = []
    for result_index, result in enumerate(results):
        if not isinstance(result, dict):
            raise EvaluationError(f"Trivy Results[{result_index}] must be an object")
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            raise EvaluationError(
                f"Trivy Results[{result_index}].Vulnerabilities must be an array"
            )
        target = result.get("Target", "")
        for finding_index, vulnerability in enumerate(vulnerabilities):
            if not isinstance(vulnerability, dict):
                location = f"{result_index}:{finding_index}"
                raise EvaluationError(
                    f"Trivy vulnerability {location} must be an object"
                )
            severity = vulnerability.get("Severity")
            if severity not in COUNTED_SEVERITIES:
                continue
            vulnerability_id = vulnerability.get("VulnerabilityID")
            if (
                not isinstance(vulnerability_id, str)
                or VULNERABILITY_ID_RE.fullmatch(vulnerability_id) is None
            ):
                raise EvaluationError(
                    f"Trivy {severity} finding has an invalid VulnerabilityID"
                )
            fixed_version = vulnerability.get("FixedVersion", "")
            if fixed_version is None:
                fixed_version = ""
            if not isinstance(fixed_version, str):
                raise EvaluationError(
                    f"Trivy finding {vulnerability_id} has a non-string FixedVersion"
                )
            findings.append(
                {
                    "id": vulnerability_id,
                    "severity": severity,
                    "fixAvailability": (
                        "available" if fixed_version.strip() else "unavailable"
                    ),
                    "target": target if isinstance(target, str) else "",
                }
            )
    return findings


def load_exceptions(
    path: Path | None,
    *,
    expected_digest: str,
    observed_ids: set[str],
    evaluated_at: dt.datetime,
) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    document = load_object(path)
    if set(document) != {"schemaVersion", "exceptions"}:
        raise EvaluationError(
            "exception document must contain only schemaVersion and exceptions"
        )
    if document.get("schemaVersion") != 1:
        raise EvaluationError("exception document schemaVersion must be 1")
    entries = document.get("exceptions")
    if not isinstance(entries, list):
        raise EvaluationError("exception document exceptions must be an array")

    required = {"imageDigest", "vulnerabilityId", "rationale", "owner", "expiresAt"}
    accepted: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != required:
            raise EvaluationError(
                f"exception {index} must contain exactly {sorted(required)}"
            )
        if not all(isinstance(entry[field], str) for field in required):
            raise EvaluationError(f"exception {index} fields must all be strings")
        digest = entry["imageDigest"]
        vulnerability_id = entry["vulnerabilityId"]
        if DIGEST_RE.fullmatch(digest) is None:
            raise EvaluationError(f"exception {index} has a malformed image digest")
        if digest != expected_digest:
            raise EvaluationError(
                f"exception {index} is for digest {digest}, not {expected_digest}"
            )
        if VULNERABILITY_ID_RE.fullmatch(vulnerability_id) is None:
            raise EvaluationError(f"exception {index} has an invalid vulnerability ID")
        if len(entry["rationale"].strip()) < 20:
            raise EvaluationError(f"exception {index} rationale is too short")
        if OWNER_RE.fullmatch(entry["owner"]) is None:
            raise EvaluationError(f"exception {index} owner is not a GitHub handle")
        expires_at = parse_utc_timestamp(
            entry["expiresAt"], field=f"exception {index} expiresAt"
        )
        if expires_at <= evaluated_at:
            raise EvaluationError(f"exception {index} expired at {entry['expiresAt']}")
        key = (digest, vulnerability_id)
        if key in seen:
            raise EvaluationError(
                f"duplicate exception for {digest} and {vulnerability_id}"
            )
        seen.add(key)
        if vulnerability_id not in observed_ids:
            raise EvaluationError(
                f"unused exception for unobserved vulnerability {vulnerability_id}"
            )
        accepted.append({field: entry[field] for field in sorted(required)})
    return sorted(accepted, key=lambda entry: entry["vulnerabilityId"])


def empty_counts() -> dict[str, dict[str, int]]:
    return {
        severity.lower(): {"available": 0, "total": 0, "unavailable": 0}
        for severity in COUNTED_SEVERITIES
    }


def evaluate(
    *,
    expected_image: str,
    report: dict[str, Any],
    subject: dict[str, Any],
    exception_path: Path | None,
    evaluated_at: str,
) -> dict[str, Any]:
    digest, report_created_at = verified_subject(expected_image, report, subject)
    report_time, _report_nanoseconds = parse_trivy_timestamp(report_created_at)
    evaluation_time = parse_utc_timestamp(evaluated_at, field="evaluatedAt")
    # The workflow's explicit evaluation time is intentionally second-precision.
    # Treat a report produced within that same second as contemporaneous.
    if evaluation_time < report_time:
        raise EvaluationError("evaluatedAt cannot precede the Trivy report")
    findings = high_critical_findings(report)
    observed_ids = {finding["id"] for finding in findings}
    exceptions = load_exceptions(
        exception_path,
        expected_digest=digest,
        observed_ids=observed_ids,
        evaluated_at=evaluation_time,
    )
    excepted_ids = {entry["vulnerabilityId"] for entry in exceptions}
    counts = empty_counts()
    unexcepted_counts = empty_counts()
    for finding in findings:
        severity = finding["severity"].lower()
        availability = finding["fixAvailability"]
        counts[severity][availability] += 1
        counts[severity]["total"] += 1
        if finding["id"] not in excepted_ids:
            unexcepted_counts[severity][availability] += 1
            unexcepted_counts[severity]["total"] += 1

    unexcepted_total = sum(value["total"] for value in unexcepted_counts.values())
    if not findings:
        outcome = "no-critical-or-high-findings"
    elif unexcepted_total == 0:
        outcome = "all-critical-high-findings-excepted"
    else:
        outcome = "critical-high-findings-require-review"

    return {
        "schemaVersion": 1,
        "subject": {"image": expected_image, "digest": digest},
        "reportCreatedAt": report_created_at,
        "evaluatedAt": evaluated_at,
        "counts": counts,
        "unexceptedCounts": unexcepted_counts,
        "exceptions": {"applied": exceptions, "count": len(exceptions)},
        "decision": {
            "mode": "evidence-only",
            "outcome": outcome,
            "promotionGate": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--scan-subject", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = evaluate(
            expected_image=args.expected_image,
            report=load_object(args.report),
            subject=load_object(args.scan_subject),
            exception_path=args.exceptions,
            evaluated_at=args.evaluated_at,
        )
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (EvaluationError, OSError, TypeError, ValueError) as error:
        print(f"Image scan evaluation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
