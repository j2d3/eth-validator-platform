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
import hashlib
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
SIGNING_GATE = "signing-disabled"

# A gate declares AWS-resource mutation and cluster-state mutation separately.
# Stopping signing merges Git changes and removes running workloads, which is a
# real mutation; it creates and bills no AWS resource. Conflating the two is how
# a contract ends up claiming a gate does nothing when it does.
GATE_FLAGS = (
    "mutates_aws",
    "mutates_cluster_state",
    "incurs_aws_cost",
    "requires_human_approval",
)

# A point-in-time restore does not carry the source's placement, networking, or
# parameters onto the target. Each of these must be supplied on the restore call
# or AWS substitutes a default, and the default DB parameter group does not
# enforce TLS.
REQUIRED_RESTORE_PARAMETERS = frozenset(
    {
        "db-subnet-group-name",
        "vpc-security-group-ids",
        "db-parameter-group-name",
        "no-publicly-accessible",
        "no-multi-az",
        "availability-zone",
        "no-deletion-protection",
    }
)

# `RestoreDBInstanceToPointInTime` has no `BackupRetentionPeriod` member, so the
# restored copy's retention cannot be chosen on the restore call. A contract that
# claimed to supply it would describe a command that fails, and the failure would
# be discovered with signing already off and a restore already billing.
UNSUPPORTED_RESTORE_PARAMETERS = frozenset({"backup-retention-period"})

# The AWS CLI expresses a boolean as a flag pair — `--multi-az | --no-multi-az`
# — and rejects a value argument. `--deletion-protection false` does not turn
# deletion protection off; it is a parse error. Requiring the disabling `no-`
# form by id, and refusing a bare boolean value, keeps the contract's restore
# call runnable as written.
DISABLED_BY_NO_PREFIX = frozenset(
    {"multi-az", "publicly-accessible", "deletion-protection"}
)
BOOLEAN_VALUE_TOKENS = frozenset({"true", "false", "yes", "no", "0", "1"})

# Continuity must cover every table that carries slashing-safety state. Counts
# and extents cannot see a replaced interior row, so each of these needs a
# per-row digest compared for exact equality.
SAFETY_BEARING_TABLES = frozenset(
    {
        "validators",
        "signed_blocks",
        "signed_attestations",
        "low_watermarks",
        "metadata",
    }
)

# Verification output must be aggregate-only. Restricting the top-level
# projection to these calls is what keeps public keys and signing roots inside
# the query session instead of inside an evidence record.
AGGREGATE_FUNCTIONS = frozenset(
    {"count", "min", "max", "sum", "avg", "bool_and", "bool_or", "md5"}
)

# The pinned Web3Signer migrations do not create `pgcrypto`, so these are not
# available on the restored copy. Reaching for one would mean installing an
# extension, which is a mutation the drill is not permitted to make.
NON_CORE_FUNCTIONS = (
    "hmac",
    "digest",
    "crypt",
    "gen_random_bytes",
    "gen_salt",
    "pgp_sym_encrypt",
)

# Continuity that accepts "at least as many rows" cannot detect a replaced or
# missing interior row. Signing is frozen before the source fingerprint, so
# there is no legitimate difference to tolerate.
INEQUALITY_PHRASES = (
    "greater than or equal",
    "at least",
    "not lower",
    "no lower",
    "or more",
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


class UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a repeated mapping key.

    PyYAML keeps the last value for a duplicated key and reports nothing, so a
    contract could declare `incurs_aws_cost` twice and be read as the opposite
    of what a reviewer saw first. A safety contract may not have a value that
    depends on which line wins.
    """


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DrillContractError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_contract(path: Path) -> dict[str, Any]:
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except DrillContractError as error:
        raise DrillContractError(f"drill contract {path} is malformed: {error}") from error
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
        for flag in GATE_FLAGS:
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
    signing_index = ordered_ids.index(SIGNING_GATE)

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
        if by_id[gate_id]["mutates_aws"] or by_id[gate_id]["incurs_aws_cost"]
    ]
    results.append(
        CheckResult(
            check_id="gates/nothing-billable-before-approval",
            passed=not premature,
            detail=(
                "no gate creates, modifies, or bills an AWS resource before the "
                "human go/no-go"
                if not premature
                else f"gates before approval that mutate AWS or bill: {premature}"
            ),
        )
    )

    signing_first = (
        signing_index < restore_index and not by_id[SIGNING_GATE]["mutates_aws"]
    )
    results.append(
        CheckResult(
            check_id="gates/signing-off-before-restore",
            passed=signing_first,
            detail=(
                "signing stops before any restore, and stopping it mutates no "
                "AWS resource"
                if signing_first
                else "signing-disabled does not precede the restore without an "
                "AWS mutation"
            ),
        )
    )

    # The honest reading of this gate: it changes Git and removes workloads. A
    # contract that called that "not mutating" would be describing a different
    # procedure from the one an operator actually runs.
    declares_workload_effect = by_id[SIGNING_GATE]["mutates_cluster_state"]
    results.append(
        CheckResult(
            check_id="gates/signing-off-declares-its-workload-effect",
            passed=declares_workload_effect,
            detail=(
                "signing-disabled declares that it changes desired state and "
                "removes running workloads"
                if declares_workload_effect
                else "signing-disabled understates its effect on cluster state"
            ),
        )
    )

    restore_declared = (
        by_id[RESTORE_GATE]["mutates_aws"] and by_id[RESTORE_GATE]["incurs_aws_cost"]
    )
    results.append(
        CheckResult(
            check_id="gates/restore-is-declared-mutating",
            passed=restore_declared,
            detail=(
                "the restore gate is declared to mutate AWS and to bill"
                if restore_declared
                else "the restore gate understates its effect"
            ),
        )
    )
    return results


def check_restore_parameters(target: dict[str, Any]) -> list[CheckResult]:
    """Require every unsafe AWS restore default to be overridden explicitly."""

    parameters = target.get("explicit_restore_parameters")
    if not isinstance(parameters, list) or not parameters:
        raise DrillContractError(
            "restore_target needs a non-empty explicit_restore_parameters list"
        )

    declared: set[str] = set()
    incomplete: list[str] = []
    unrunnable: list[str] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            raise DrillContractError("each restore parameter must be a mapping")
        parameter_id = parameter.get("id")
        if not isinstance(parameter_id, str) or not parameter_id:
            raise DrillContractError("each restore parameter needs a string id")
        if parameter_id in declared:
            raise DrillContractError(f"duplicate restore parameter {parameter_id!r}")
        declared.add(parameter_id)
        # `default_if_omitted` is required because it is what the parameter is
        # being weighed against. A parameter without it reads like a preference
        # rather than a reviewed decision about an AWS default.
        if not all(
            isinstance(parameter.get(field), str) and parameter[field].strip()
            for field in ("value", "default_if_omitted")
        ):
            incomplete.append(parameter_id)
        # A boolean written as `--deletion-protection false` is a parse error,
        # not a setting, and a parameter the restore API does not accept is a
        # call that cannot be made.
        value = str(parameter.get("value", "")).strip().lower()
        if (
            parameter_id in DISABLED_BY_NO_PREFIX
            or parameter_id in UNSUPPORTED_RESTORE_PARAMETERS
            or value in BOOLEAN_VALUE_TOKENS
        ):
            unrunnable.append(parameter_id)

    missing = sorted(REQUIRED_RESTORE_PARAMETERS - declared)
    verification = target.get("post_restore_verification")
    verified = isinstance(verification, list) and len(verification) >= 4

    # A parameter the API does not accept has to be accounted for, not merely
    # left out. Requiring the contract to say why it is absent, and what handles
    # it instead, is what keeps a later edit from quietly reinstating it.
    absences = target.get("not_supplied_on_restore_call")
    documented_absences = {
        entry["id"]
        for entry in (absences if isinstance(absences, list) else [])
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and str(entry.get("reason") or "").strip()
        and str(entry.get("handled_by") or "").strip()
    }
    unexplained = sorted(UNSUPPORTED_RESTORE_PARAMETERS - documented_absences)

    if missing:
        detail = f"restore parameters not supplied explicitly: {missing}"
    elif unexplained:
        detail = (
            "parameters the restore API does not accept are neither supplied nor "
            f"explained: {unexplained}"
        )
    elif unrunnable:
        detail = (
            "restore parameters that AWS would not accept as written: "
            f"{sorted(unrunnable)}. A CLI boolean is a flag pair such as "
            "`--no-deletion-protection`, never a flag with a true/false value, "
            "and RestoreDBInstanceToPointInTime accepts no backup retention "
            "parameter"
        )
    elif incomplete:
        detail = f"restore parameters without a value and stated AWS default: {sorted(incomplete)}"
    elif not verified:
        detail = "the restored copy's properties are not re-verified after the restore"
    else:
        detail = (
            "every network, parameter-group, placement, and protection value is "
            "supplied on the restore call in runnable CLI syntax and re-verified "
            "afterwards"
        )

    return [
        CheckResult(
            check_id="targets/restore-parameters-are-explicit",
            passed=not missing
            and not unexplained
            and not unrunnable
            and not incomplete
            and verified,
            detail=detail,
        )
    ]


def check_target_credentials(target: dict[str, Any]) -> list[CheckResult]:
    """Require the drill to reach the restored copy without touching live credentials."""

    credentials = target.get("credentials")
    if not isinstance(credentials, dict):
        raise DrillContractError("restore_target needs a credentials mapping")
    drill_secret = credentials.get("drill_secret")
    master = credentials.get("master_credential")
    if not isinstance(drill_secret, dict) or not isinstance(master, dict):
        raise DrillContractError(
            "credentials needs drill_secret and master_credential mappings"
        )

    live_untouched = (
        credentials.get("live_secret_mutation") == "forbidden"
        and credentials.get("live_external_secret_repoint") == "forbidden"
    )
    drill_path_declared = (
        drill_secret.get("deleted_at_cleanup") is True
        and bool(str(drill_secret.get("target_name") or "").strip())
        and bool(str(drill_secret.get("source_object") or "").strip())
        and bool(str(drill_secret.get("managed_by") or "").strip())
    )
    master_untouched = master.get("read_by_operator") is False

    if not live_untouched:
        detail = "the drill does not forbid mutating or repointing the live credential"
    elif not drill_path_declared:
        detail = "the drill-only connection Secret's origin and disposal are not stated"
    elif not master_untouched:
        detail = "the drill would read the restored copy's master credential"
    else:
        detail = (
            "the drill connects as the restored application role through a "
            "drill-only Secret; the live credential is neither mutated nor "
            "repointed and no master password is read"
        )

    return [
        CheckResult(
            check_id="targets/target-credentials-are-isolated",
            passed=live_untouched and drill_path_declared and master_untouched,
            detail=detail,
        ),
        *check_drill_connection_split(credentials),
    ]


# The Secrets Manager connection object holds the *live* host. An ExternalSecret
# that projected the whole object into a drill Secret would re-assert that live
# host on every refresh, so an operator-supplied endpoint written over it would
# be silently reverted mid-drill. The connection is therefore assembled from two
# objects with disjoint ownership, and this check is what keeps them disjoint.
CREDENTIAL_PROJECTION = frozenset({"username", "password", "database", "port"})
HOST_KEY_NAMES = frozenset({"host", "hostname", "endpoint", "db_host", "dbhost"})


def check_drill_connection_split(credentials: dict[str, Any]) -> list[CheckResult]:
    """Require a reconciliation-stable split of host from credential fields."""

    drill_secret = credentials["drill_secret"]
    config_map = credentials.get("drill_endpoint_config_map")
    consumption = credentials.get("consumption")
    if not isinstance(config_map, dict) or not isinstance(consumption, dict):
        raise DrillContractError(
            "credentials needs drill_endpoint_config_map and consumption mappings"
        )

    def key_set(value: Any) -> set[str]:
        return {str(key).lower() for key in value} if isinstance(value, list) else set()

    projected_set = key_set(drill_secret.get("projected_keys"))
    excluded_set = key_set(drill_secret.get("excluded_keys"))

    secret_is_credentials_only = (
        projected_set == CREDENTIAL_PROJECTION
        and not projected_set & HOST_KEY_NAMES
        and bool(excluded_set & HOST_KEY_NAMES)
        and bool(str(drill_secret.get("exclusion_reason") or "").strip())
    )
    config_map_declared = (
        bool(str(config_map.get("name") or "").strip())
        and bool(str(config_map.get("key") or "").strip())
        and bool(str(config_map.get("value_source") or "").strip())
        and bool(str(config_map.get("reconciled_by") or "").strip())
        and config_map.get("committed_to_git") is False
        and config_map.get("written_back_to_secrets_manager") is False
        and config_map.get("deleted_at_cleanup") is True
    )
    consumers = consumption.get("consumers")
    split_consumed = (
        consumption.get("host_from") == "drill_endpoint_config_map"
        and consumption.get("credential_fields_from") == "drill_secret"
        and bool(str(consumption.get("mechanism") or "").strip())
        and isinstance(consumers, list)
        and len(consumers) >= 2
    )

    if not secret_is_credentials_only:
        detail = (
            "the drill Secret does not project exactly the credential fields "
            f"{sorted(CREDENTIAL_PROJECTION)} with the host excluded, so External "
            "Secrets Operator would re-assert the live host on refresh"
        )
    elif not config_map_declared:
        detail = (
            "the restored endpoint has no drill-only ConfigMap that is "
            "uncommitted, unreconciled, and deleted at cleanup"
        )
    elif not split_consumed:
        detail = (
            "the drill workloads do not take the host and the credential fields "
            "from separate objects"
        )
    else:
        detail = (
            "the host comes from a drill-only ConfigMap and the credential "
            "fields from a drill-only Secret, each with a single writer, and "
            "both are deleted at cleanup"
        )

    return [
        CheckResult(
            check_id="targets/drill-connection-split-is-reconciliation-stable",
            passed=secret_is_credentials_only and config_map_declared and split_consumed,
            detail=detail,
        )
    ]


def check_target_backups(target: dict[str, Any]) -> list[CheckResult]:
    """Require the restored copy to leave no recovery points of its own behind."""

    backup = target.get("backup")
    if not isinstance(backup, dict):
        raise DrillContractError("restore_target needs a backup mapping")

    flags = backup.get("delete_flags")
    retention_zero = backup.get("target_backup_retention_days") == 0
    deletes_backups = isinstance(flags, list) and {
        "skip-final-snapshot",
        "delete-automated-backups",
    }.issubset(set(flags))
    accounted = bool(backup.get("verified_by")) and backup.get(
        "retained_after_cleanup"
    ) == "nothing"

    if not retention_zero:
        detail = "the restored copy would keep automated backups of slashing history"
    elif not deletes_backups:
        detail = "deletion does not skip the final snapshot and drop automated backups"
    elif not accounted:
        detail = "target backup state is not verified, or something is retained after cleanup"
    else:
        detail = (
            "the restored copy takes no backups of its own and leaves nothing "
            "behind after cleanup"
        )

    return [
        CheckResult(
            check_id="targets/target-backups-are-not-retained",
            passed=retention_zero and deletes_backups and accounted,
            detail=detail,
        ),
        *check_backup_mismatch_fails_closed(backup),
    ]


# Retention is not a parameter of the restore call, so it is observed rather than
# chosen. Observing something other than zero means the call that ran was not the
# call the approver reviewed. Modifying the target at that point would overwrite
# the evidence of the discrepancy with the intended value, which is why the
# response is declared as an enum rather than as prose a check has to interpret.
ABORT_AND_CLEANUP = "abort-and-cleanup"


def check_backup_mismatch_fails_closed(backup: dict[str, Any]) -> list[CheckResult]:
    """Require an unexpected retention value to abort, not to be repaired in place."""

    on_mismatch = backup.get("on_mismatch")
    if not isinstance(on_mismatch, dict):
        raise DrillContractError("backup.on_mismatch must be a mapping")

    aborts = on_mismatch.get("action") == ABORT_AND_CLEANUP
    repairs = on_mismatch.get("modify_target") is not False
    reasoned = bool(str(on_mismatch.get("rationale") or "").strip())
    # Claiming to supply retention on the restore call is the other way this goes
    # wrong: the restore API has no such parameter, so the claim could only be
    # discovered as a failed command mid-drill.
    honest_about_the_api = backup.get("supplied_on_restore_call") is False

    if not honest_about_the_api:
        detail = (
            "the contract claims the copy's backup retention is supplied on the "
            "restore call, which RestoreDBInstanceToPointInTime does not accept"
        )
    elif repairs:
        detail = (
            "a backup-retention mismatch would modify the just-reviewed target "
            "instead of aborting"
        )
    elif not aborts or not reasoned:
        detail = (
            "a backup-retention mismatch does not declare "
            f"action: {ABORT_AND_CLEANUP} with a stated rationale"
        )
    else:
        detail = (
            "an unexpected backup retention value aborts the drill and runs "
            "cleanup; the just-reviewed target is never modified after the restore"
        )

    return [
        CheckResult(
            check_id="targets/backup-retention-mismatch-fails-closed",
            passed=honest_about_the_api and aborts and reasoned and not repairs,
            detail=detail,
        )
    ]


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
        *check_restore_parameters(target),
        *check_target_credentials(target),
        *check_target_backups(target),
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
    for function in NON_CORE_FUNCTIONS:
        if re.search(rf"\b{function}\s*\(", lowered):
            return CheckResult(
                check_id=f"query/{query_id}",
                passed=False,
                detail=(
                    f"query calls {function}(), which the pinned migrations do "
                    "not provide; only PostgreSQL core functions are available"
                ),
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

    results.extend(check_schema_evidence(schema))
    results.extend(check_continuity_coverage(continuity, seen))

    pass_conditions = continuity.get("pass_conditions")
    results.append(
        CheckResult(
            check_id="verification/continuity-pass-conditions",
            passed=isinstance(pass_conditions, list) and len(pass_conditions) >= 4,
            detail="continuity has explicit pass conditions rather than an opinion",
        )
    )
    return results


def table_inventory_digest(tables: Iterable[str]) -> str:
    """The digest `table-inventory-digest` must return: md5 of the sorted names.

    Python's sort is codepoint order, which is what `COLLATE "C"` gives the
    query, so the two sides cannot disagree about ordering.
    """

    return hashlib.md5(",".join(sorted(tables)).encode("utf-8")).hexdigest()


def check_schema_evidence(schema: dict[str, Any]) -> list[CheckResult]:
    """Require schema evidence that pins the exact objects, not a table count."""

    tables = schema.get("expected_tables")
    declared_digest = schema.get("expected_table_digest")
    named = (
        isinstance(tables, list)
        and len(tables) >= 5
        and all(isinstance(table, str) and table for table in tables)
        and len(set(tables)) == len(tables)
    )
    digest_matches = named and declared_digest == table_inventory_digest(tables)

    results = [
        CheckResult(
            check_id="verification/schema-inventory-digest",
            passed=digest_matches and bool(schema.get("table_digest_definition")),
            detail=(
                "the expected table inventory is pinned by a digest recomputed "
                "from the declared table set, not by a count"
                if digest_matches and bool(schema.get("table_digest_definition"))
                else "the declared table digest does not match the declared table set"
            ),
        )
    ]

    migration_count = schema.get("expected_migration_count")
    database_version = schema.get("expected_database_version")
    query_ids = {
        query.get("id") for query in schema.get("queries", []) if isinstance(query, dict)
    }
    # Flyway's own history and Web3Signer's `database_version` row are separate
    # claims. A restored copy can carry a complete Flyway history and still be a
    # schema version the signer refuses, so both are required.
    anchored = (
        isinstance(migration_count, int)
        and not isinstance(migration_count, bool)
        and migration_count > 0
        and migration_count == database_version
        and {"applied-migration-history", "database-version-row"}.issubset(query_ids)
    )
    results.append(
        CheckResult(
            check_id="verification/schema-version-anchors",
            passed=anchored,
            detail=(
                "the Flyway history and Web3Signer's own database_version are "
                "both pinned to the pinned image's migration count"
                if anchored
                else "the migration count and database_version anchors disagree "
                "or are not queried"
            ),
        )
    )

    objects = schema.get("slashing_critical_objects")
    described: set[str] = set()
    well_formed = isinstance(objects, list) and bool(objects)
    if well_formed:
        for entry in objects:
            if not isinstance(entry, dict):
                well_formed = False
                break
            table = entry.get("table")
            keys = entry.get("unique_keys")
            if not isinstance(table, str) or not isinstance(keys, list) or not keys:
                well_formed = False
                break
            for key in keys:
                if (
                    not isinstance(key, dict)
                    or not isinstance(key.get("columns"), list)
                    or not key["columns"]
                    or not str(key.get("enforces") or "").strip()
                ):
                    well_formed = False
                    break
            described.add(table)
    covered = well_formed and SAFETY_BEARING_TABLES.issubset(described)
    named_tables = set(tables) if named else set()
    consistent = covered and described.issubset(named_tables)
    results.append(
        CheckResult(
            check_id="verification/slashing-critical-objects-declared",
            passed=consistent,
            detail=(
                "every safety-bearing table declares the uniqueness the pinned "
                "migrations give it, and what that uniqueness enforces"
                if consistent
                else "the slashing-critical object set is incomplete or names a "
                "table outside the expected inventory"
            ),
        )
    )
    return results


def check_continuity_coverage(
    continuity: dict[str, Any], query_ids: set[str]
) -> list[CheckResult]:
    """Require an exact per-row digest for every safety-bearing table."""

    covered = continuity.get("covered_tables")
    covered_set = set(covered) if isinstance(covered, list) else set()
    uncovered = sorted(SAFETY_BEARING_TABLES - covered_set)
    missing_queries = sorted(
        table
        for table in covered_set
        if f"{table.replace('_', '-')}-digest" not in query_ids
    )

    if uncovered:
        detail = f"safety-bearing tables with no continuity coverage: {uncovered}"
    elif missing_queries:
        detail = f"covered tables with no digest query: {missing_queries}"
    else:
        detail = (
            "every safety-bearing table is reduced to a per-row digest, so a "
            "replaced or missing interior row cannot pass"
        )
    results = [
        CheckResult(
            check_id="verification/continuity-covers-every-safety-table",
            passed=not uncovered and not missing_queries,
            detail=detail,
        )
    ]

    conditions = continuity.get("pass_conditions")
    tolerant = [
        condition
        for condition in (conditions if isinstance(conditions, list) else [])
        if any(phrase in str(condition).lower() for phrase in INEQUALITY_PHRASES)
    ]
    exact = continuity.get("comparison") == "exact-equality" and not tolerant
    results.append(
        CheckResult(
            check_id="verification/continuity-requires-exact-equality",
            passed=exact,
            detail=(
                "continuity is exact equality; signing is frozen before the "
                "fingerprint, so no difference is legitimate"
                if exact
                else "continuity tolerates a difference between the source and "
                "the restored copy"
            ),
        )
    )

    salt = continuity.get("salt_parameter")
    queries = continuity.get("queries") if isinstance(continuity.get("queries"), list) else []
    unsalted = sorted(
        str(query.get("id"))
        for query in queries
        if isinstance(query, dict)
        and str(query.get("id", "")).endswith("-digest")
        and isinstance(salt, str)
        and salt not in str(query.get("sql", ""))
    )
    salted = (
        isinstance(salt, str)
        and salt.startswith(":")
        and bool(continuity.get("salt_handling"))
        and not unsalted
    )
    results.append(
        CheckResult(
            check_id="verification/digests-are-salted",
            passed=salted,
            detail=(
                "every row digest is salted with a session-only per-drill value, "
                "so no committed digest is a lookup table for a public key"
                if salted
                else f"digest queries without the per-drill salt: {unsalted}"
            ),
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
