#!/usr/bin/env python3
"""Aggregate per-image evidence decisions into one public-safe count summary."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


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


def parse_timestamp(value: object, *, path: Path) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AggregateError(f"{path} evaluatedAt must be a UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise AggregateError(f"{path} evaluatedAt is invalid") from error
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


def load_inventory(path: Path) -> dict[str, Any]:
    document = load_object(path)
    if document.get("schemaVersion") != 1:
        raise AggregateError(f"{path} is not a schemaVersion 1 inventory")
    for field in ("images", "coverageGaps"):
        if not isinstance(document.get(field), list):
            raise AggregateError(f"{path} field {field!r} is not a list")
    return document


def inventory_subject_set(inventory: dict[str, Any]) -> set[tuple[str, str]]:
    expected: set[tuple[str, str]] = set()
    for entry in inventory["images"]:
        if not isinstance(entry, dict):
            raise AggregateError("inventory image entry is not an object")
        image = entry.get("image")
        digest = entry.get("digest")
        if not isinstance(image, str) or not isinstance(digest, str):
            raise AggregateError("inventory image is missing string image/digest")
        subject = (image, digest)
        if subject in expected:
            raise AggregateError(f"duplicate image subject in inventory: {image}")
        expected.add(subject)
    return expected


def aggregate(root: Path, inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = sorted(root.glob("image-scan-*/image-scan-decision.json"))
    if not paths:
        raise AggregateError("no image-scan decision artifacts were found")

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
        image = subject.get("image")
        digest = subject.get("digest")
        if not isinstance(image, str) or not image or not isinstance(digest, str):
            raise AggregateError(f"{path} subject image and digest must be strings")
        subject_key = (image, digest)
        if subject_key in subjects:
            raise AggregateError(f"duplicate image subject in {path}: {image}")
        subjects.add(subject_key)

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

    latest = max(evaluated_at).astimezone(dt.timezone.utc)
    summary: dict[str, Any] = {
        "schemaVersion": 1,
        "evaluatedAt": latest.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "images": len(subjects),
        "counts": counts,
        "unexceptedCounts": unexcepted_counts,
        "decision": {"mode": "evidence-only", "promotionGate": False},
    }

    if inventory is not None:
        expected = inventory_subject_set(inventory)
        missing = sorted({image for image, _ in expected - subjects})
        extra = sorted({image for image, _ in subjects - expected})
        if missing:
            raise AggregateError(
                "missing image-scan decision(s) for inventory subject(s): "
                + ", ".join(missing)
            )
        if extra:
            raise AggregateError(
                "unexpected image-scan decision(s) not in inventory: "
                + ", ".join(extra)
            )
        gaps = inventory["coverageGaps"]
        for entry in gaps:
            if not isinstance(entry, dict):
                raise AggregateError("inventory coverageGap entry is not an object")
        gap_kinds: dict[str, int] = {}
        for entry in gaps:
            kind = entry.get("kind")
            if not isinstance(kind, str):
                raise AggregateError("inventory coverageGap kind must be a string")
            gap_kinds[kind] = gap_kinds.get(kind, 0) + 1
        summary["exactSubjects"] = {
            "scanned": len(subjects),
            "expected": len(expected),
        }
        summary["coverageGaps"] = {
            "count": len(gaps),
            "kinds": dict(sorted(gap_kinds.items())),
        }

    return summary


def github_outputs(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    lines = [
        "available=true",
        f"images={summary['images']}",
        f"critical_total={counts['critical']['total']}",
        f"critical_available={counts['critical']['available']}",
        f"high_total={counts['high']['total']}",
        f"high_available={counts['high']['available']}",
    ]
    if "exactSubjects" in summary:
        lines.append(f"exact_scanned={summary['exactSubjects']['scanned']}")
        lines.append(f"exact_expected={summary['exactSubjects']['expected']}")
    if "coverageGaps" in summary:
        lines.append(f"coverage_gaps={summary['coverageGaps']['count']}")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--inventory",
        type=Path,
        help="Bind aggregation to a schemaVersion 1 discovery inventory; "
        "reject missing/extra decisions and publish coverage-gap count.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        inventory = load_inventory(args.inventory) if args.inventory else None
        summary = aggregate(args.root, inventory=inventory)
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
