#!/usr/bin/env python3
"""Validate desired-state schemas and cross-document safety invariants."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = ROOT / "applications"
SCHEMAS = ROOT / "schemas"
SCHEMA_FILES = {
    "Customer": "customer.schema.json",
    "ServiceProfile": "service-profile.schema.json",
    "ValidatorIdentity": "validator-identity.schema.json",
    "ValidatorAssignment": "validator-assignment.schema.json",
}
LIVE_ASSIGNMENT_STATES = {"activating", "active", "failed-safe", "stopping", "switching"}


def load_yaml_documents() -> list[tuple[Path, dict[str, Any]]]:
    documents: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(APPLICATIONS.rglob("*.yaml")):
        with path.open(encoding="utf-8") as stream:
            for document in yaml.safe_load_all(stream):
                if document is not None:
                    documents.append((path, document))
    return documents


def load_validators() -> dict[str, Draft202012Validator]:
    validators: dict[str, Draft202012Validator] = {}
    for kind, filename in SCHEMA_FILES.items():
        with (SCHEMAS / filename).open(encoding="utf-8") as stream:
            schema = json.load(stream)
        Draft202012Validator.check_schema(schema)
        validators[kind] = Draft202012Validator(schema)
    return validators


def schema_errors(
    documents: list[tuple[Path, dict[str, Any]]],
    validators: dict[str, Draft202012Validator],
) -> list[str]:
    errors: list[str] = []
    for path, document in documents:
        kind = document.get("kind")
        validator = validators.get(kind)
        if validator is None:
            errors.append(f"{path.relative_to(ROOT)}: unsupported kind {kind!r}")
            continue
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            errors.append(f"{path.relative_to(ROOT)}:{location}: {error.message}")
    return errors


def relational_errors(documents: list[tuple[Path, dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    by_kind: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for path, document in documents:
        kind = document.get("kind", "")
        name = document.get("metadata", {}).get("name", "")
        if name in by_kind[kind]:
            errors.append(f"duplicate {kind} metadata.name {name!r}")
            continue
        by_kind[kind][name] = document
    public_keys: dict[str, str] = {}
    customer_ids: dict[str, str] = {}
    for name, customer in by_kind["Customer"].items():
        customer_id = customer["spec"]["customerId"]
        if customer_id in customer_ids:
            errors.append(f"Customer/{name}: customerId duplicates Customer/{customer_ids[customer_id]}")
        customer_ids[customer_id] = name

    for name, identity in by_kind["ValidatorIdentity"].items():
        spec = identity["spec"]
        customer = spec["customerRef"]
        if customer not in by_kind["Customer"]:
            errors.append(f"ValidatorIdentity/{name}: unknown customerRef {customer!r}")
        public_key = spec.get("publicKey")
        if public_key:
            normalized = public_key.lower()
            if normalized in public_keys:
                errors.append(
                    f"ValidatorIdentity/{name}: publicKey duplicates ValidatorIdentity/{public_keys[normalized]}"
                )
            public_keys[normalized] = name

    live_assignments: dict[str, list[str]] = defaultdict(list)
    live_node_pairs: dict[str, list[str]] = defaultdict(list)
    for name, assignment in by_kind["ValidatorAssignment"].items():
        spec = assignment["spec"]
        validator_name = spec["validatorRef"]
        profile_name = spec["serviceProfileRef"]
        identity = by_kind["ValidatorIdentity"].get(validator_name)
        profile = by_kind["ServiceProfile"].get(profile_name)
        if identity is None:
            errors.append(f"ValidatorAssignment/{name}: unknown validatorRef {validator_name!r}")
        if profile is None:
            errors.append(f"ValidatorAssignment/{name}: unknown serviceProfileRef {profile_name!r}")
        if spec["lifecycle"] in LIVE_ASSIGNMENT_STATES:
            live_assignments[validator_name].append(name)
            node_pair = spec.get("nodePairRef")
            if node_pair:
                live_node_pairs[node_pair].append(name)
        if spec["signingEnabled"]:
            if identity and identity["spec"]["synthetic"]:
                errors.append(f"ValidatorAssignment/{name}: synthetic identity may not sign")
            if identity and identity["spec"]["lifecycle"] != "registered":
                errors.append(f"ValidatorAssignment/{name}: signing requires a registered identity")
            if identity:
                customer = by_kind["Customer"].get(identity["spec"]["customerRef"])
                if customer and customer["spec"]["lifecycle"] != "active":
                    errors.append(f"ValidatorAssignment/{name}: signing requires an active customer")
            if profile and not profile["spec"]["signingAllowed"]:
                errors.append(f"ValidatorAssignment/{name}: profile does not allow signing")

    for validator_name, assignment_names in live_assignments.items():
        if len(assignment_names) > 1:
            errors.append(
                f"ValidatorIdentity/{validator_name}: multiple live assignments: {', '.join(sorted(assignment_names))}"
            )

    for node_pair, assignment_names in live_node_pairs.items():
        if len(assignment_names) > 1:
            errors.append(
                f"NodePair/{node_pair}: multiple live assignments: {', '.join(sorted(assignment_names))}"
            )

    for customer_name, customer in by_kind["Customer"].items():
        if customer["spec"]["lifecycle"] not in {"offboarding", "offboarded"}:
            continue
        owned = {
            name
            for name, identity in by_kind["ValidatorIdentity"].items()
            if identity["spec"]["customerRef"] == customer_name
        }
        active_owned = sorted(name for name in owned if live_assignments.get(name))
        if active_owned:
            errors.append(
                f"Customer/{customer_name}: offboarding with live validator assignments: {', '.join(active_owned)}"
            )

    return errors


def main() -> int:
    documents = load_yaml_documents()
    errors = schema_errors(documents, load_validators())
    if not errors:
        errors.extend(relational_errors(documents))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(documents)} desired-state documents and relational safety invariants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
