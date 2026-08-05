#!/usr/bin/env python3
"""Aggregate per-image evidence decisions into one public-safe count summary.

Aggregation is bound to the exact inventory discovered for the same source
revision. The scanned ``(image, digest)`` subjects must equal the discovered
exact subjects: a missing, extra, or duplicated decision is an error rather
than a quietly smaller count. The summary also carries the number of
unresolved image sources so that partial coverage is published alongside the
findings instead of reading as complete.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"^(?:sha256:)?(?P<digest>[0-9a-f]{64})$")
PINNED_IMAGE_RE = re.compile(
    r"^(?P<repository>[A-Za-z0-9][A-Za-z0-9._/:\-]*?)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)


class AggregateError(ValueError):
    """Raised when per-image evidence cannot be aggregated safely."""


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AggregateError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise AggregateError(f"{path} must contain a JSON object")
    return value


def parse_timestamp(
    value: object, *, path: Path, field: str = "evaluatedAt"
) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AggregateError(f"{path} {field} must be a UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise AggregateError(f"{path} {field} is invalid") from error
    return parsed


def count_block(value: object, *, path: Path, field: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {
        "available",
        "total",
        "unavailable",
    }:
        raise AggregateError(f"{path} {field} has an invalid count shape")
    if any(type(value[key]) is not int or value[key] < 0 for key in value):
        raise AggregateError(f"{path} {field} counts must be non-negative integers")
    if value["available"] + value["unavailable"] != value["total"]:
        raise AggregateError(f"{path} {field} counts do not add up")
    return {key: value[key] for key in ("available", "total", "unavailable")}


def subject_key(image: object, digest: object, *, origin: str) -> tuple[str, str]:
    """Return the canonical ``(image, sha256:digest)`` identity of one subject."""

    if not isinstance(image, str) or not isinstance(digest, str):
        raise AggregateError(f"{origin} subject image and digest must be strings")
    pinned = PINNED_IMAGE_RE.fullmatch(image)
    if pinned is None:
        raise AggregateError(f"{origin} subject {image!r} is not pinned by sha256 digest")
    declared = DIGEST_RE.fullmatch(digest)
    if declared is None:
        raise AggregateError(f"{origin} subject digest {digest!r} is malformed")
    if declared.group("digest") != pinned.group("digest"):
        raise AggregateError(f"{origin} subject digest does not match {image!r}")
    return image, f"sha256:{pinned.group('digest')}"


def load_inventory(path: Path) -> tuple[set[tuple[str, str]], int]:
    """Return the discovered exact subjects and unresolved coverage-gap count."""

    inventory = load_object(path)
    if inventory.get("schemaVersion") != 1:
        raise AggregateError(f"{path} schemaVersion must be 1")

    records = inventory.get("images")
    if not isinstance(records, list) or not records:
        raise AggregateError(f"{path} must list at least one discovered image")

    subjects: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict):
            raise AggregateError(f"{path} image entries must be objects")
        key = subject_key(record.get("image"), record.get("digest"), origin=str(path))
        if key in subjects:
            raise AggregateError(f"{path} lists duplicate image subject {key[0]}")
        subjects.add(key)

    gaps = inventory.get("coverageGaps")
    if not isinstance(gaps, list) or not all(isinstance(gap, dict) for gap in gaps):
        raise AggregateError(f"{path} coverageGaps must be an array of objects")

    return subjects, len(gaps)


def aggregate(root: Path, inventory_path: Path) -> dict[str, Any]:
    expected_subjects, coverage_gaps = load_inventory(inventory_path)

    paths = sorted(root.glob("image-scan-*/image-scan-decision.json"))
    if not paths:
        raise AggregateError("no image-scan decision artifacts were found")
    sbom_paths = sorted(root.glob("image-scan-*/sbom-subject.json"))
    if not sbom_paths:
        raise AggregateError("no verified SBOM subject artifacts were found")

    counts = {
        severity: {"available": 0, "total": 0, "unavailable": 0}
        for severity in ("critical", "high")
    }
    unexcepted_counts = {
        severity: {"available": 0, "total": 0, "unavailable": 0}
        for severity in ("critical", "high")
    }
    subjects: set[tuple[str, str]] = set()
    evaluated_at: list[dt.datetime] = []

    for path in paths:
        decision = load_object(path)
        if decision.get("schemaVersion") != 1:
            raise AggregateError(f"{path} schemaVersion must be 1")
        subject = decision.get("subject")
        if not isinstance(subject, dict):
            raise AggregateError(f"{path} subject must be an object")
        key = subject_key(subject.get("image"), subject.get("digest"), origin=str(path))
        if key in subjects:
            raise AggregateError(f"duplicate image subject in {path}: {key[0]}")
        subjects.add(key)

        outcome = decision.get("decision")
        if not isinstance(outcome, dict) or outcome.get("mode") != "evidence-only":
            raise AggregateError(f"{path} is not an evidence-only decision")
        if outcome.get("promotionGate") is not False:
            raise AggregateError(f"{path} unexpectedly enables a promotion gate")
        evaluated_at.append(parse_timestamp(decision.get("evaluatedAt"), path=path))

        for field, target in (
            ("counts", counts),
            ("unexceptedCounts", unexcepted_counts),
        ):
            source = decision.get(field)
            if not isinstance(source, dict) or set(source) != {"critical", "high"}:
                raise AggregateError(f"{path} {field} must contain critical and high")
            for severity in ("critical", "high"):
                block = count_block(source[severity], path=path, field=f"{field}.{severity}")
                for key in target[severity]:
                    target[severity][key] += block[key]

    missing = sorted(image for image, _ in expected_subjects - subjects)
    if missing:
        raise AggregateError(
            "discovered subjects have no evidence decision: " + ", ".join(missing)
        )
    extra = sorted(image for image, _ in subjects - expected_subjects)
    if extra:
        raise AggregateError(
            "evidence decisions are not in the discovered inventory: " + ", ".join(extra)
        )

    sbom_subjects: set[tuple[str, str]] = set()
    for path in sbom_paths:
        sbom = load_object(path)
        if sbom.get("schemaVersion") != 1 or sbom.get("format") != "CycloneDX":
            raise AggregateError(f"{path} is not verified CycloneDX SBOM evidence")
        key = subject_key(sbom.get("image"), sbom.get("digest"), origin=str(path))
        if key in sbom_subjects:
            raise AggregateError(f"duplicate SBOM subject in {path}: {key[0]}")
        sbom_subjects.add(key)
        if type(sbom.get("componentCount")) is not int or sbom["componentCount"] < 0:
            raise AggregateError(f"{path} componentCount must be a non-negative integer")
        parse_timestamp(sbom.get("generatedAt"), path=path, field="generatedAt")

    missing_sboms = sorted(image for image, _ in expected_subjects - sbom_subjects)
    if missing_sboms:
        raise AggregateError(
            "discovered subjects have no verified SBOM: " + ", ".join(missing_sboms)
        )
    extra_sboms = sorted(image for image, _ in sbom_subjects - expected_subjects)
    if extra_sboms:
        raise AggregateError(
            "verified SBOMs are not in the discovered inventory: "
            + ", ".join(extra_sboms)
        )

    latest = max(evaluated_at).astimezone(dt.timezone.utc)
    return {
        "schemaVersion": 3,
        "evaluatedAt": latest.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "exactSubjects": len(expected_subjects),
        "scannedSubjects": len(subjects),
        "sbomSubjects": len(sbom_subjects),
        "coverageGaps": coverage_gaps,
        "counts": counts,
        "unexceptedCounts": unexcepted_counts,
        "decision": {"mode": "evidence-only", "promotionGate": False},
    }


def github_outputs(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    return "\n".join(
        [
            "available=true",
            f"exact_subjects={summary['exactSubjects']}",
            f"scanned_subjects={summary['scannedSubjects']}",
            f"sbom_subjects={summary['sbomSubjects']}",
            f"coverage_gaps={summary['coverageGaps']}",
            f"critical_total={counts['critical']['total']}",
            f"critical_available={counts['critical']['available']}",
            f"high_total={counts['high']['total']}",
            f"high_available={counts['high']['available']}",
        ]
    ) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = aggregate(args.root, args.inventory)
        args.output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write(github_outputs(summary))
    except AggregateError as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
