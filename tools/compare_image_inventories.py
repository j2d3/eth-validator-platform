#!/usr/bin/env python3
"""Decide whether an exact container-image inventory needs a fresh scan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class InventoryComparisonError(ValueError):
    """Raised when an inventory cannot be compared safely."""


def load_inventory(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InventoryComparisonError(f"cannot read inventory {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise InventoryComparisonError(f"{path} is not a schemaVersion 1 inventory")
    for field in ("images", "coverageGaps", "scopeExclusions"):
        if not isinstance(document.get(field), list):
            raise InventoryComparisonError(f"{path} field {field!r} is not a list")
    return document


def comparison_surface(document: dict[str, Any]) -> dict[str, Any]:
    images: list[tuple[str, str]] = []
    for image in document["images"]:
        if not isinstance(image, dict):
            raise InventoryComparisonError("inventory image entry is not an object")
        image_id = image.get("id")
        image_ref = image.get("image")
        if not isinstance(image_id, str) or not isinstance(image_ref, str):
            raise InventoryComparisonError("inventory image is missing string id/image")
        images.append((image_id, image_ref))

    def canonical_rows(field: str) -> list[str]:
        rows = document[field]
        if not all(isinstance(row, dict) for row in rows):
            raise InventoryComparisonError(f"inventory {field} entry is not an object")
        return sorted(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)

    return {
        "images": sorted(images),
        "coverageGaps": canonical_rows("coverageGaps"),
        "scopeExclusions": canonical_rows("scopeExclusions"),
    }


def compare(base: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    base_surface = comparison_surface(base)
    current_surface = comparison_surface(current)
    changed_fields = [
        field for field in base_surface if base_surface[field] != current_surface[field]
    ]
    if changed_fields:
        return {
            "scanRequired": True,
            "decision": "inventory changed: " + ", ".join(changed_fields),
        }
    return {
        "scanRequired": False,
        "decision": "exact image subjects and coverage boundaries are unchanged",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "github-output"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = compare(load_inventory(args.base), load_inventory(args.current))
    except InventoryComparisonError as error:
        print(f"inventory comparison failed: {error}", file=sys.stderr)
        return 1

    if args.format == "github-output":
        print(f"scan_required={str(result['scanRequired']).lower()}")
        print(f"decision={result['decision']}")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
