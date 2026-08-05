#!/usr/bin/env python3
"""Check the RDS slashing-recovery drill contract and emit redacted readiness.

This tool is deliberately inert. It reads the checked-in drill contract, the
Terraform declarations under `terraform/environments/dev`, and the drill
runbook, then reports whether the drill is safe to *propose*. It opens no AWS
API call, no PostgreSQL connection, and no secret. It performs no restore and
simulates none: a readiness report is not recovery evidence.

Every string it prints is scanned for disclosure classes before it reaches
stdout, so a contract edit cannot turn this into an identifier leak.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "hack" / "qualification" / "rds-slashing-recovery-drill.yaml"

EXPECTED_API_VERSION = "platform.galaxy-lab/v1"
EXPECTED_KIND = "SlashingRecoveryDrillContract"

# The drill's safety argument is an order, not a checklist. Signing stops before
# anything is recovered, a human decides before anything is billed, and the
# restored copy is proven before it is ever asked to refuse a signature.
REQUIRED_GATE_ORDER = (
    "signing-disabled",
    "source-fingerprint",
    "human-go-no-go",
    "restore-isolated-target",
    "schema-compatibility",
    "row-continuity",
    "conflicting-duty-rejection",
    "cleanup",
    "evidence",
)
APPROVAL_GATE = "human-go-no-go"
RESTORE_GATE = "restore-isolated-target"

# Verification output must be aggregate-only. Restricting the top-level
# projection to these calls is what keeps public keys and signing roots inside
# the query session instead of inside an evidence record.
AGGREGATE_FUNCTIONS = frozenset(
    {"count", "min", "max", "sum", "avg", "bool_and", "bool_or", "md5"}
)
WRITE_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "copy",
    "merge",
    "call",
    "vacuum",
    "reindex",
    "lock",
    "into",
)

DISCLOSURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-account-id", re.compile(r"\b\d{12}\b")),
    ("aws-arn", re.compile(r"arn:aws[a-z-]*:", re.IGNORECASE)),
    ("rds-endpoint-hostname", re.compile(r"[A-Za-z0-9.-]+\.rds\.amazonaws\.com")),
    ("jdbc-connection-string", re.compile(r"jdbc:postgresql://", re.IGNORECASE)),
    ("ip-address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("bls-public-key", re.compile(r"\b0x[0-9a-fA-F]{96}\b")),
    (
        "secret-value",
        re.compile(
            r"(?i)\b(?:password|secret_string|private_key|keystore_password)\s*[:=]\s*\S"
        ),
    ),
)


class DrillContractError(RuntimeError):
    """The drill contract cannot be proven safe to propose."""


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    detail: str


def load_contract(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise DrillContractError(f"cannot read drill contract {path}: {error}") from error
    if not isinstance(document, dict):
        raise DrillContractError(f"drill contract {path} must contain a mapping")
    if document.get("apiVersion") != EXPECTED_API_VERSION:
        raise DrillContractError(
            f"drill contract apiVersion must be {EXPECTED_API_VERSION!r}"
        )
    if document.get("kind") != EXPECTED_KIND:
        raise DrillContractError(f"drill contract kind must be {EXPECTED_KIND!r}")
    return document


def terraform_block(text: str, scope: str) -> str:
    """Return one whitespace-compacted top-level Terraform block body."""

    kind, _, name = scope.partition(":")
    if kind == "variable":
        header = rf'variable "{re.escape(name)}"'
    elif kind == "resource":
        resource_type, _, resource_name = name.partition(".")
        if not resource_type or not resource_name:
            raise DrillContractError(f"malformed resource scope {scope!r}")
        header = rf'resource "{re.escape(resource_type)}" "{re.escape(resource_name)}"'
    else:
        raise DrillContractError(f"unsupported guard scope {scope!r}")

    match = re.search(
        rf"^{header} \{{(?P<body>.*?)(?=^(?:resource|data|locals|module|output|variable|moved) )",
        text + "\nmoved ",
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise DrillContractError(f"Terraform block {scope!r} is not declared")
    return " ".join(match.group("body").split())


def check_terraform_guards(
    contract: dict[str, Any], terraform_root: Path
) -> list[CheckResult]:
    guards = contract.get("required_terraform_guards")
    if not isinstance(guards, list) or not guards:
        raise DrillContractError("required_terraform_guards must be a non-empty list")

    sources: dict[str, str] = {}
    results: list[CheckResult] = []
    for guard in guards:
        if not isinstance(guard, dict):
            raise DrillContractError("each Terraform guard must be a mapping")
        guard_id = guard.get("id")
        filename = guard.get("terraform_file")
        scope = guard.get("scope")
        expected = guard.get("expect")
        if not all(
            isinstance(value, str) and value
            for value in (guard_id, filename, scope, expected)
        ):
            raise DrillContractError(
                "each Terraform guard needs id, terraform_file, scope, and expect"
            )
        if not isinstance(guard.get("rationale"), str) or not guard["rationale"].strip():
            raise DrillContractError(f"guard {guard_id!r} needs a rationale")

        if filename not in sources:
            path = terraform_root / filename
            try:
                sources[filename] = path.read_text(encoding="utf-8")
            except OSError as error:
                raise DrillContractError(f"cannot read {path}: {error}") from error

        body = terraform_block(sources[filename], scope)
        passed = expected in body
        results.append(
            CheckResult(
                check_id=f"terraform-guard/{guard_id}",
                passed=passed,
                detail=(
                    f"{scope} declares {expected!r}"
                    if passed
                    else f"{scope} does not declare {expected!r}"
                ),
            )
        )
    return results


def check_gates(contract: dict[str, Any]) -> list[CheckResult]:
    gates = contract.get("gates")
    if not isinstance(gates, list) or not gates:
        raise DrillContractError("gates must be a non-empty list")

    ordered_ids: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for gate in gates:
        if not isinstance(gate, dict):
            raise DrillContractError("each gate must be a mapping")
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not gate_id:
            raise DrillContractError("each gate needs a string id")
        if gate_id in by_id:
            raise DrillContractError(f"duplicate gate id {gate_id!r}")
        for flag in ("mutating", "incurs_aws_cost", "requires_human_approval"):
            if not isinstance(gate.get(flag), bool):
                raise DrillContractError(f"gate {gate_id!r} needs a boolean {flag}")
        if not isinstance(gate.get("summary"), str) or not gate["summary"].strip():
            raise DrillContractError(f"gate {gate_id!r} needs a summary")
        ordered_ids.append(gate_id)
        by_id[gate_id] = gate

    results = [
        CheckResult(
            check_id="gates/required-order",
            passed=ordered_ids == list(REQUIRED_GATE_ORDER),
            detail=(
                "gate sequence matches the required order"
                if ordered_ids == list(REQUIRED_GATE_ORDER)
                else f"gate sequence is {ordered_ids}, expected {list(REQUIRED_GATE_ORDER)}"
            ),
        )
    ]
    if not results[0].passed:
        # Every remaining ordering claim is derived from positions, so stop here
        # rather than reporting a cascade of misleading failures.
        return results

    approval_index = ordered_ids.index(APPROVAL_GATE)
    restore_index = ordered_ids.index(RESTORE_GATE)
    signing_index = ordered_ids.index("signing-disabled")

    approvers = [
        gate_id for gate_id, gate in by_id.items() if gate["requires_human_approval"]
    ]
    results.append(
        CheckResult(
            check_id="gates/single-human-approval",
            passed=approvers == [APPROVAL_GATE],
            detail=(
                f"{APPROVAL_GATE} is the only human approval gate"
                if approvers == [APPROVAL_GATE]
                else f"human approval gates are {approvers}"
            ),
        )
    )

    premature = [
        gate_id
        for gate_id in ordered_ids[:approval_index]
        if by_id[gate_id]["mutating"] or by_id[gate_id]["incurs_aws_cost"]
    ]
    results.append(
        CheckResult(
            check_id="gates/nothing-billable-before-approval",
            passed=not premature,
            detail=(
                "no gate mutates or bills before the human go/no-go"
                if not premature
                else f"gates before approval that mutate or bill: {premature}"
            ),
        )
    )

    results.append(
        CheckResult(
            check_id="gates/signing-off-before-restore",
            passed=signing_index < restore_index
            and not by_id["signing-disabled"]["mutating"],
            detail=(
                "signing is disabled, non-mutatingly, before any restore"
                if signing_index < restore_index
                and not by_id["signing-disabled"]["mutating"]
                else "signing-disabled does not precede the restore as a non-mutating gate"
            ),
        )
    )

    results.append(
        CheckResult(
            check_id="gates/restore-is-declared-mutating",
            passed=by_id[RESTORE_GATE]["mutating"]
            and by_id[RESTORE_GATE]["incurs_aws_cost"],
            detail=(
                "the restore gate is declared mutating and billable"
                if by_id[RESTORE_GATE]["mutating"]
                and by_id[RESTORE_GATE]["incurs_aws_cost"]
                else "the restore gate understates its effect"
            ),
        )
    )
    return results


def check_targets(contract: dict[str, Any]) -> list[CheckResult]:
    source = contract.get("source")
    target = contract.get("restore_target")
    if not isinstance(source, dict) or not isinstance(target, dict):
        raise DrillContractError("source and restore_target must both be mappings")

    network = target.get("network")
    lifecycle = target.get("lifecycle")
    if not isinstance(network, dict) or not isinstance(lifecycle, dict):
        raise DrillContractError("restore_target needs network and lifecycle mappings")

    max_lifetime = lifecycle.get("max_lifetime_hours")
    identifier_template = target.get("identifier_template")

    return [
        CheckResult(
            check_id="targets/source-is-read-only",
            passed=source.get("mutation_allowed") is False
            and source.get("access") == "read-only",
            detail="the source instance is declared read-only",
        ),
        CheckResult(
            check_id="targets/restore-is-a-separate-instance",
            passed=target.get("must_differ_from_source") is True
            and isinstance(identifier_template, str)
            and "{source_identifier}" in identifier_template
            and identifier_template != "{source_identifier}",
            detail="the restore target derives a distinct identifier from the source",
        ),
        CheckResult(
            check_id="targets/restore-stays-private",
            passed=target.get("publicly_accessible") is False
            and network.get("ingress_from_live_signer_group") == "forbidden"
            and network.get("ingress_from_live_migration_group") == "forbidden",
            detail=(
                "the restored copy is private and unreachable from the live signer "
                "and migration paths"
            ),
        ),
        CheckResult(
            check_id="targets/restore-is-time-bounded",
            passed=lifecycle.get("delete_after_drill") is True
            and isinstance(max_lifetime, int)
            and 0 < max_lifetime <= 24,
            detail="the restored copy has a bounded lifetime and a mandatory delete",
        ),
    ]


def top_level_projection(sql: str) -> list[str]:
    """Split a single SELECT's outermost projection into its items."""

    compact = " ".join(sql.split())
    if not re.match(r"(?i)^select\s", compact):
        raise DrillContractError(f"query must begin with SELECT: {compact[:40]!r}")

    depth = 0
    from_index: int | None = None
    for match in re.finditer(r"[()]|\bfrom\b", compact[6:], flags=re.IGNORECASE):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            from_index = match.start() + 6
            break
    if from_index is None:
        raise DrillContractError(f"query has no top-level FROM: {compact[:40]!r}")

    projection = compact[6:from_index]
    items: list[str] = []
    depth = 0
    current = ""
    for character in projection:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        current += character
    if current.strip():
        items.append(current.strip())
    return items


def check_query_is_aggregate_only(
    query_id: str, sql: str, forbidden_columns: Iterable[str]
) -> CheckResult:
    compact = " ".join(sql.split())
    lowered = compact.lower()

    if ";" in compact:
        return CheckResult(
            check_id=f"query/{query_id}",
            passed=False,
            detail="query contains a statement separator",
        )
    for keyword in WRITE_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            return CheckResult(
                check_id=f"query/{query_id}",
                passed=False,
                detail=f"query is not read-only; it contains {keyword.upper()}",
            )

    try:
        items = top_level_projection(compact)
    except DrillContractError as error:
        return CheckResult(check_id=f"query/{query_id}", passed=False, detail=str(error))

    forbidden = {name.lower() for name in forbidden_columns}
    for item in items:
        alias = re.search(r"(?i)\bas\s+([A-Za-z_][A-Za-z0-9_]*)$", item)
        if alias is None:
            return CheckResult(
                check_id=f"query/{query_id}",
                passed=False,
                detail=f"projection item is not explicitly aliased: {item!r}",
            )
        if alias.group(1).lower() in forbidden:
            return CheckResult(
                check_id=f"query/{query_id}",
                passed=False,
                detail=f"projection emits a forbidden column: {alias.group(1)!r}",
            )
        function = re.match(r"(?i)^([A-Za-z_][A-Za-z0-9_]*)\s*\(", item)
        if function is None or function.group(1).lower() not in AGGREGATE_FUNCTIONS:
            return CheckResult(
                check_id=f"query/{query_id}",
                passed=False,
                detail=f"projection item is not an aggregate or digest call: {item!r}",
            )

    return CheckResult(
        check_id=f"query/{query_id}",
        passed=True,
        detail="read-only and aggregate-only",
    )


def check_verification(contract: dict[str, Any]) -> list[CheckResult]:
    verification = contract.get("verification")
    if not isinstance(verification, dict):
        raise DrillContractError("verification must be a mapping")
    schema = verification.get("schema")
    continuity = verification.get("continuity")
    if not isinstance(schema, dict) or not isinstance(continuity, dict):
        raise DrillContractError("verification needs schema and continuity mappings")

    forbidden = continuity.get("forbidden_result_columns")
    if not isinstance(forbidden, list) or not forbidden:
        raise DrillContractError("continuity needs forbidden_result_columns")

    results: list[CheckResult] = []
    seen: set[str] = set()
    for section in (schema, continuity):
        queries = section.get("queries")
        if not isinstance(queries, list) or not queries:
            raise DrillContractError("each verification section needs queries")
        for query in queries:
            if not isinstance(query, dict):
                raise DrillContractError("each query must be a mapping")
            query_id = query.get("id")
            sql = query.get("sql")
            if not isinstance(query_id, str) or not isinstance(sql, str):
                raise DrillContractError("each query needs a string id and sql")
            if query_id in seen:
                raise DrillContractError(f"duplicate query id {query_id!r}")
            seen.add(query_id)
            results.append(check_query_is_aggregate_only(query_id, sql, forbidden))

    expected_tables = schema.get("expected_tables")
    results.append(
        CheckResult(
            check_id="verification/schema-inventory-declared",
            passed=isinstance(expected_tables, list)
            and len(expected_tables) >= 5
            and isinstance(schema.get("expected_migration_version"), str),
            detail="the expected table set and applied migration version are declared",
        )
    )

    pass_conditions = continuity.get("pass_conditions")
    results.append(
        CheckResult(
            check_id="verification/continuity-pass-conditions",
            passed=isinstance(pass_conditions, list) and len(pass_conditions) >= 4,
            detail="continuity has explicit pass conditions rather than an opinion",
        )
    )
    return results


def check_rejection_test(contract: dict[str, Any]) -> list[CheckResult]:
    rejection = contract.get("rejection_test")
    if not isinstance(rejection, dict):
        raise DrillContractError("rejection_test must be a mapping")
    signer = rejection.get("signer")
    expected = rejection.get("expected_result")
    if not isinstance(signer, dict) or not isinstance(expected, dict):
        raise DrillContractError("rejection_test needs signer and expected_result")

    return [
        CheckResult(
            check_id="rejection/no-fleet-key",
            passed=rejection.get("live_fleet_key_use") == "forbidden"
            and rejection.get("key_class") == "drill-only",
            detail="the rejection test uses a drill-only key, never a fleet key",
        ),
        CheckResult(
            check_id="rejection/no-live-signing-path",
            passed=signer.get("database") == "the restored copy only"
            and signer.get("beacon_connection") == "forbidden"
            and signer.get("validator_client_connection") == "forbidden"
            and signer.get("publication") == "forbidden",
            detail=(
                "the drill signer is bound to the restored copy and cannot publish"
            ),
        ),
        CheckResult(
            check_id="rejection/expects-a-refusal",
            passed=expected.get("second_request_http_status") == 412
            and expected.get("prevented_counter_delta") == 1,
            detail="a conflicting duty must be refused and counted as prevented",
        ),
    ]


def check_operational_completeness(contract: dict[str, Any]) -> list[CheckResult]:
    status = contract.get("status")
    cost = contract.get("cost")
    failure = contract.get("failure_handling")
    evidence = contract.get("evidence")
    for name, value in (
        ("status", status),
        ("cost", cost),
        ("failure_handling", failure),
        ("evidence", evidence),
    ):
        if not isinstance(value, dict):
            raise DrillContractError(f"{name} must be a mapping")

    abort_conditions = failure.get("abort_conditions")
    on_abort = failure.get("on_abort")
    redaction = evidence.get("redaction")
    estimate = cost.get("estimated_total_usd_max")

    return [
        CheckResult(
            check_id="status/nothing-executed-or-authorized",
            passed=status.get("executed") is False
            and status.get("mutating_actions_authorized") is False
            and status.get("evidence_record") is None,
            detail="the contract records no execution and authorizes no mutation",
        ),
        CheckResult(
            check_id="cost/bounded-estimate",
            passed=isinstance(estimate, (int, float))
            and 0 < float(estimate) <= 50
            and isinstance(cost.get("billable_items"), list)
            and bool(cost.get("cost_control")),
            detail="the drill declares a bounded cost estimate and a cost control",
        ),
        CheckResult(
            check_id="failure/abort-and-recovery-declared",
            passed=isinstance(abort_conditions, list)
            and len(abort_conditions) >= 5
            and isinstance(on_abort, list)
            and len(on_abort) >= 3
            and bool(failure.get("signing_restoration")),
            detail="abort conditions, abort actions, and signing restoration are declared",
        ),
        CheckResult(
            check_id="evidence/redaction-classes-declared",
            passed=isinstance(redaction, dict)
            and isinstance(redaction.get("forbidden_classes"), list)
            and {
                name for name, _ in DISCLOSURE_PATTERNS
            }.issubset(set(redaction["forbidden_classes"])),
            detail="the evidence record forbids every disclosure class this tool scans for",
        ),
    ]


def check_runbook(contract: dict[str, Any], root: Path) -> list[CheckResult]:
    metadata = contract.get("metadata")
    if not isinstance(metadata, dict):
        raise DrillContractError("metadata must be a mapping")
    relative = metadata.get("runbook")
    if not isinstance(relative, str) or not relative:
        raise DrillContractError("metadata.runbook must name the drill runbook")

    path = root / relative
    if not path.is_file():
        return [
            CheckResult(
                check_id="runbook/present",
                passed=False,
                detail=f"{relative} does not exist",
            )
        ]
    text = path.read_text(encoding="utf-8")
    missing = [
        gate["id"]
        for gate in contract["gates"]
        if isinstance(gate, dict) and gate.get("id") not in text
    ]
    return [
        CheckResult(check_id="runbook/present", passed=True, detail=f"{relative} exists"),
        CheckResult(
            check_id="runbook/documents-every-gate",
            passed=not missing,
            detail=(
                "the runbook documents every contract gate"
                if not missing
                else f"the runbook omits gates: {missing}"
            ),
        ),
    ]


def scan_for_disclosure(text: str) -> list[str]:
    """Return the disclosure classes present in text, most specific first."""

    return sorted(name for name, pattern in DISCLOSURE_PATTERNS if pattern.search(text))


def run_checks(contract: dict[str, Any], root: Path) -> list[CheckResult]:
    source = contract.get("source")
    if not isinstance(source, dict) or not isinstance(
        source.get("terraform_root"), str
    ):
        raise DrillContractError("source.terraform_root must name a Terraform root")
    terraform_root = root / source["terraform_root"]
    return [
        *check_gates(contract),
        *check_targets(contract),
        *check_terraform_guards(contract, terraform_root),
        *check_verification(contract),
        *check_rejection_test(contract),
        *check_operational_completeness(contract),
        *check_runbook(contract, root),
    ]


def render_markdown(contract: dict[str, Any], results: list[CheckResult]) -> str:
    metadata = contract["metadata"]
    failures = [result for result in results if not result.passed]
    lines = [
        "# RDS slashing-recovery drill readiness",
        "",
        f"Contract `{metadata['name']}` revision {metadata['revision']}, "
        f"environment `{metadata['environment']}`.",
        "",
        "This is a readiness report produced from checked-in declarations. "
        "No AWS API call, database connection, secret read, or restore was "
        "performed to produce it. It is not recovery evidence.",
        "",
        f"**Result**: {len(results) - len(failures)} of {len(results)} checks passed.",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| `{result.check_id}` | {'pass' if result.passed else 'FAIL'} | "
            f"{result.detail} |"
        )
    lines += [
        "",
        "## Authorization state",
        "",
        "- drill executed: no",
        "- mutating actions authorized: no",
        f"- estimated maximum cost if authorized: USD {contract['cost']['estimated_total_usd_max']:.2f}",
        "- a restore requires an explicit human go/no-go recorded at the "
        f"`{APPROVAL_GATE}` gate",
    ]
    return "\n".join(lines) + "\n"


def render_json(contract: dict[str, Any], results: list[CheckResult]) -> str:
    payload = {
        "contract": contract["metadata"]["name"],
        "revision": contract["metadata"]["revision"],
        "environment": contract["metadata"]["environment"],
        "executed": False,
        "mutating_actions_authorized": False,
        "estimated_total_usd_max": contract["cost"]["estimated_total_usd_max"],
        "checks": [
            {"id": result.check_id, "passed": result.passed, "detail": result.detail}
            for result in results
        ],
        "passed": all(result.passed for result in results),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def build_report(contract: dict[str, Any], root: Path, output_format: str) -> tuple[str, bool]:
    results = run_checks(contract, root)
    report = (
        render_markdown(contract, results)
        if output_format == "markdown"
        else render_json(contract, results)
    )
    disclosed = scan_for_disclosure(report)
    if disclosed:
        raise DrillContractError(
            "refusing to emit readiness evidence containing disclosure classes: "
            + ", ".join(disclosed)
        )
    return report, all(result.passed for result in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--contract",
        type=Path,
        default=DEFAULT_CONTRACT,
        help="path to the drill contract (default: the checked-in contract)",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="readiness evidence format",
    )
    arguments = parser.parse_args(argv)

    try:
        contract = load_contract(arguments.contract)
        report, passed = build_report(contract, ROOT, arguments.format)
    except DrillContractError as error:
        print(f"drill preflight failed: {error}", file=sys.stderr)
        return 1

    sys.stdout.write(report)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
