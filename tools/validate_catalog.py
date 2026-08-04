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
    "NetworkProfile": "network-profile.schema.json",
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

    for name, network_profile in by_kind["NetworkProfile"].items():
        spec = network_profile["spec"]
        adapter_modes = {
            adapter["mode"]
            for adapter in (
                *spec["clients"].values(),
                spec["signer"]["web3signer"],
            )
        }
        if len(adapter_modes) > 1:
            errors.append(
                f"NetworkProfile/{name}: client and signer adapter modes disagree: "
                f"{', '.join(sorted(adapter_modes))}"
            )
        if spec["resetPolicy"] == "replace-data":
            expected_name = f"{spec['family']}-{spec['generation']}"
            if name != expected_name:
                errors.append(
                    f"NetworkProfile/{name}: replace-data profile name must be "
                    f"generation-addressed as {expected_name!r}"
                )
            if adapter_modes != {"artifact-bundle"}:
                errors.append(
                    f"NetworkProfile/{name}: replace-data profiles require artifact-bundle "
                    "client and signer adapters"
                )
        artifact = spec.get("artifactBundle")
        client_artifact_mode = any(
            adapter["mode"] == "artifact-bundle" for adapter in spec["clients"].values()
        )
        if client_artifact_mode and spec["resetPolicy"] != "replace-data":
            errors.append(
                f"NetworkProfile/{name}: artifact client adapters require resetPolicy "
                "'replace-data'"
            )
        if client_artifact_mode and "checkpointSync" not in spec:
            errors.append(
                f"NetworkProfile/{name}: artifact client adapters require checkpointSync"
            )
        if artifact and "/latest/" in artifact["url"].lower():
            errors.append(
                f"NetworkProfile/{name}: artifactBundle.url must pin a generation, not /latest/"
            )
        if artifact:
            expected_release_path = f"/releases/download/{name}/"
            if expected_release_path not in artifact["url"]:
                errors.append(
                    f"NetworkProfile/{name}: artifactBundle.url must contain immutable "
                    f"release path {expected_release_path!r}"
                )
            paths = list(artifact["files"].values())
            if len(paths) != len(set(paths)):
                errors.append(
                    f"NetworkProfile/{name}: artifactBundle.files paths must be unique"
                )
            for path_name, path in artifact["files"].items():
                if ".." in Path(path).parts:
                    errors.append(
                        f"NetworkProfile/{name}: artifactBundle.files.{path_name} "
                        "may not traverse directories"
                    )
        builtin_networks = {
            adapter["network"]
            for adapter in (
                *spec["clients"].values(),
                spec["signer"]["web3signer"],
            )
            if adapter["mode"] == "builtin"
        }
        if len(builtin_networks) > 1:
            errors.append(
                f"NetworkProfile/{name}: built-in client and signer adapters "
                f"disagree: {', '.join(sorted(builtin_networks))}"
            )
        if builtin_networks and spec["family"] in {"hoodi", "sepolia"}:
            builtin_network = next(iter(builtin_networks))
            if builtin_network != spec["family"]:
                errors.append(
                    f"NetworkProfile/{name}: built-in adapter {builtin_network!r} "
                    f"does not match family {spec['family']!r}"
                )

    for name, identity in by_kind["ValidatorIdentity"].items():
        spec = identity["spec"]
        customer = spec["customerRef"]
        if customer not in by_kind["Customer"]:
            errors.append(f"ValidatorIdentity/{name}: unknown customerRef {customer!r}")
        network_profile = spec["networkProfileRef"]
        if network_profile not in by_kind["NetworkProfile"]:
            errors.append(
                f"ValidatorIdentity/{name}: unknown networkProfileRef {network_profile!r}"
            )
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
        network_profile_name = spec["networkProfileRef"]
        profile_name = spec["serviceProfileRef"]
        identity = by_kind["ValidatorIdentity"].get(validator_name)
        network_profile = by_kind["NetworkProfile"].get(network_profile_name)
        profile = by_kind["ServiceProfile"].get(profile_name)
        if identity is None:
            errors.append(f"ValidatorAssignment/{name}: unknown validatorRef {validator_name!r}")
        if profile is None:
            errors.append(f"ValidatorAssignment/{name}: unknown serviceProfileRef {profile_name!r}")
        if network_profile is None:
            errors.append(
                f"ValidatorAssignment/{name}: unknown networkProfileRef {network_profile_name!r}"
            )
        if identity and identity["spec"]["networkProfileRef"] != network_profile_name:
            errors.append(
                f"ValidatorAssignment/{name}: networkProfileRef {network_profile_name!r} "
                f"does not match ValidatorIdentity/{validator_name} binding "
                f"{identity['spec']['networkProfileRef']!r}"
            )
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
            if (
                network_profile
                and network_profile["spec"]["signer"]["web3signer"]["mode"]
                != "builtin"
                and not network_profile["spec"]["signer"]["web3signer"].get(
                    "signingQualified", False
                )
            ):
                errors.append(
                    f"ValidatorAssignment/{name}: signing is not qualified for "
                    f"NetworkProfile/{network_profile_name}"
                )

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
