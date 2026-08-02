#!/usr/bin/env python3
"""Request a safe non-signing node-pair lifecycle transition in Git.

The tool updates one ValidatorAssignment and regenerates the local Flux
projection. It intentionally exposes only activate/stop and refuses any
assignment that already has signing enabled; runtime activation gates and
validator duties are separate, later work.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    from tools import render_local_assignments, validate_catalog
except ModuleNotFoundError:  # Direct execution: python3 tools/set_node_pair_lifecycle.py
    import render_local_assignments  # type: ignore[no-redef]
    import validate_catalog  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
ASSIGNMENT_DIRECTORY = ROOT / "applications" / "validators" / "assignments"
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
TRANSITIONS = {
    ("stopped", "activate"): "active",
    ("active", "stop"): "stopped",
}


class LifecycleError(ValueError):
    """The requested catalog transition is invalid or outside this slice."""


def find_assignment_path(assignment_name: str) -> Path:
    matches: list[Path] = []
    for path in sorted(ASSIGNMENT_DIRECTORY.glob("*.yaml")):
        with path.open(encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
        if document and document.get("metadata", {}).get("name") == assignment_name:
            matches.append(path)
    if not matches:
        raise LifecycleError(f"unknown ValidatorAssignment {assignment_name!r}")
    if len(matches) > 1:
        raise LifecycleError(f"duplicate ValidatorAssignment {assignment_name!r}")
    return matches[0]


def transition_assignment(
    assignment: dict[str, Any],
    action: str,
    reason: str,
) -> dict[str, Any]:
    """Mutate and return one assignment after enforcing the initial contract."""

    reason = reason.strip()
    if not 3 <= len(reason) <= 256:
        raise LifecycleError("reason must contain between 3 and 256 characters")
    spec = assignment["spec"]
    if spec.get("signingEnabled"):
        raise LifecycleError(
            "this workflow cannot manage an assignment with signing enabled; use the qualified stop path"
        )
    current = spec["lifecycle"]
    target = TRANSITIONS.get((current, action))
    if target is None:
        allowed = sorted(candidate for state, candidate in TRANSITIONS if state == current)
        suffix = f"; allowed action(s): {', '.join(allowed)}" if allowed else ""
        raise LifecycleError(f"cannot {action} assignment from lifecycle {current!r}{suffix}")

    spec["lifecycle"] = target
    if action == "activate":
        spec.setdefault(
            "nodePairRef",
            render_local_assignments.default_node_pair_ref(spec["validatorRef"]),
        )
    # Belt-and-braces: this initial path can launch EL + beacon node only.
    spec["signingEnabled"] = False
    spec["safety"] = {
        "slashingProtectionConfirmed": False,
        "doppelgangerProtectionConfirmed": False,
    }
    spec["maintenance"] = {"reason": reason}
    return assignment


def validate_documents_with(
    replacement_path: Path,
    replacement: dict[str, Any],
) -> list[str]:
    documents = validate_catalog.load_yaml_documents()
    replaced = False
    for index, (path, _) in enumerate(documents):
        if path == replacement_path:
            documents[index] = (path, replacement)
            replaced = True
            break
    if not replaced:
        raise LifecycleError(f"assignment path is outside the desired-state catalog: {replacement_path}")
    errors = validate_catalog.schema_errors(documents, validate_catalog.load_validators())
    if not errors:
        errors.extend(validate_catalog.relational_errors(documents))
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", required=True, help="ValidatorAssignment metadata.name")
    parser.add_argument("--action", required=True, choices=("activate", "stop"))
    parser.add_argument("--reason", required=True, help="auditable maintenance/request reason")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not NAME_PATTERN.fullmatch(args.assignment):
        print("ERROR: assignment must be a DNS-safe catalog name", file=sys.stderr)
        return 2

    try:
        assignment_path = find_assignment_path(args.assignment)
        with assignment_path.open(encoding="utf-8") as stream:
            assignment = yaml.safe_load(stream)
        transition_assignment(assignment, args.action, args.reason)
        errors = validate_documents_with(assignment_path, assignment)
        if errors:
            raise LifecycleError("; ".join(errors))

        catalog = render_local_assignments.load_catalog()
        catalog["ValidatorAssignment"][args.assignment] = assignment
        generated = render_local_assignments.rendered_files(catalog)
    except (LifecycleError, render_local_assignments.ProjectionError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    assignment_path.write_text(
        yaml.safe_dump(assignment, sort_keys=False, width=120),
        encoding="utf-8",
    )
    render_local_assignments.write_projection(generated)
    print(
        f"Requested {args.action}: ValidatorAssignment/{args.assignment} is now "
        f"{assignment['spec']['lifecycle']!r}; signing remains disabled."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
