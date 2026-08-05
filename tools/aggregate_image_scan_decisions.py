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


def aggregate(root: Path) -> dict[str, Any]:
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
    return {
        "schemaVersion": 1,
        "evaluatedAt": latest.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "images": len(subjects),
        "counts": counts,
        "unexceptedCounts": unexcepted_counts,
        "decision": {"mode": "evidence-only", "promotionGate": False},
    }


def github_outputs(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    return "\n".join(
        [
            "available=true",
            f"images={summary['images']}",
            f"critical_total={counts['critical']['total']}",
            f"critical_available={counts['critical']['available']}",
            f"high_total={counts['high']['total']}",
            f"high_available={counts['high']['available']}",
        ]
    ) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = aggregate(args.root)
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
