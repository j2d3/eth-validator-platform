#!/usr/bin/env python3
"""Run one interactive validator-key ceremony into a collision-safe directory.

This wrapper does not accept a mnemonic, keystore password, or withdrawal
address on its command line. The pinned deposit CLI owns those interactive
prompts. The wrapper only chooses and validates the output location, then
checks the two generated public artifacts without printing key material.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
NETWORK_PROFILE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
VALIDATOR_ID_PATTERN = re.compile(r"^validator-[a-z][a-z0-9-]{2,62}$")
PUBLIC_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{96}$")
FORK_VERSION_PATTERN = re.compile(r"^[0-9a-fA-F]{8}$")
DEPOSIT_AMOUNT_GWEI = 32_000_000_000


class CeremonyError(RuntimeError):
    """A bounded local validation or deposit-CLI failure."""


def default_chain(network_profile: str) -> str:
    """Map a generation-addressed profile to the deposit CLI's chain name."""

    return re.sub(r"-[0-9]+$", "", network_profile)


def load_network_contract(
    network_profile: str, profile_root: Path
) -> tuple[str, str]:
    path = profile_root / f"{network_profile}.yaml"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("profile is not an object")
        if value["kind"] != "NetworkProfile":
            raise ValueError("document is not a NetworkProfile")
        if value["metadata"]["name"] != network_profile:
            raise ValueError("metadata.name does not match the requested profile")
        family = value["spec"]["family"]
        fork_version = value["spec"]["identity"]["genesisForkVersion"]
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise CeremonyError(
            f"Cannot load the committed NetworkProfile {network_profile!r}: {exc}"
        ) from exc
    if not isinstance(family, str) or not NETWORK_PROFILE_PATTERN.fullmatch(family):
        raise CeremonyError("NetworkProfile family is absent or invalid")
    if not isinstance(fork_version, str):
        raise CeremonyError("NetworkProfile genesis fork version is absent")
    normalized_fork = fork_version.removeprefix("0x")
    if not FORK_VERSION_PATTERN.fullmatch(normalized_fork):
        raise CeremonyError("NetworkProfile genesis fork version is invalid")
    return family, normalized_fork.lower()


def resolve_deposit_cli(explicit: Path | None, tools_root: Path) -> Path:
    if explicit is not None:
        candidates = [explicit.expanduser().resolve()]
    else:
        candidates = sorted(
            path.resolve()
            for path in tools_root.expanduser().glob(
                "ethstaker-deposit-cli-*/bin/**/deposit"
            )
            if path.is_file() and os.access(path, os.X_OK)
        )
    if not candidates:
        raise CeremonyError(
            "Deposit CLI not found; pass --deposit-cli or install it under "
            f"{display_path(tools_root)}"
        )
    if len(candidates) != 1:
        rendered = ", ".join(display_path(path) for path in candidates)
        raise CeremonyError(
            "Multiple deposit CLI executables found; select one with "
            f"--deposit-cli: {rendered}"
        )
    candidate = candidates[0]
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise CeremonyError(
            f"Deposit CLI is not an executable file: {display_path(candidate)}"
        )
    return candidate


def display_path(path: Path) -> str:
    try:
        return f"~/{path.expanduser().resolve().relative_to(Path.home().resolve())}"
    except ValueError:
        return str(path)


def prepare_target(output_root: Path, network_profile: str, validator_id: str) -> Path:
    target = output_root.expanduser().resolve() / network_profile / validator_id
    if target.exists() and any(target.iterdir()):
        raise CeremonyError(
            "Validator output directory is not empty; refusing to mix or overwrite "
            f"key material: {display_path(target)}"
        )
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.chmod(0o700)
    return target


def run_deposit_cli(
    executable: Path,
    target: Path,
    chain: str,
) -> None:
    command = [
        str(executable),
        "--language",
        "English",
        "new-mnemonic",
        "--num_validators",
        "1",
        "--folder",
        str(target),
        "--chain",
        chain,
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise CeremonyError(
            "Deposit CLI failed; generated files, if any, were left in the isolated "
            f"directory {display_path(target)} for inspection"
        ) from exc


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CeremonyError(
            f"Generated artifact is not readable JSON: {display_path(path)}"
        ) from exc


def one_artifact(target: Path, pattern: str, label: str) -> Path:
    matches = sorted(target.rglob(pattern))
    if len(matches) != 1:
        raise CeremonyError(
            f"Expected exactly one {label} below {display_path(target)}; found "
            f"{len(matches)}"
        )
    return matches[0]


def require_private_file(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise CeremonyError(
            f"Generated artifact is group/world accessible ({mode:04o}): "
            f"{display_path(path)}"
        )


def validate_artifacts(
    target: Path,
    chain: str,
    expected_fork_version: str,
) -> tuple[Path, Path]:
    keystore_path = one_artifact(target, "keystore-*.json", "encrypted keystore")
    deposit_path = one_artifact(target, "deposit_data-*.json", "deposit-data file")
    require_private_file(keystore_path)
    require_private_file(deposit_path)

    keystore = read_json(keystore_path)
    if not isinstance(keystore, dict) or keystore.get("version") != 4:
        raise CeremonyError("Generated keystore is not one EIP-2335 version-4 object")
    public_key = str(keystore.get("pubkey", "")).removeprefix("0x")
    if not PUBLIC_KEY_PATTERN.fullmatch(public_key):
        raise CeremonyError("Generated keystore does not contain one 48-byte BLS key")
    if not isinstance(keystore.get("crypto"), dict):
        raise CeremonyError("Generated keystore crypto object is absent")

    deposit = read_json(deposit_path)
    if not isinstance(deposit, list) or len(deposit) != 1:
        raise CeremonyError("Deposit-data file must contain exactly one validator")
    record = deposit[0]
    if not isinstance(record, dict):
        raise CeremonyError("Deposit-data validator record is invalid")
    if record.get("pubkey") != public_key:
        raise CeremonyError("Deposit-data public key does not match the keystore")
    if record.get("network_name") != chain:
        raise CeremonyError(
            "Deposit-data network does not match the requested deposit CLI chain"
        )
    if record.get("amount") != DEPOSIT_AMOUNT_GWEI:
        raise CeremonyError("Deposit-data amount is not exactly 32 ETH")
    observed_fork_version = str(record.get("fork_version", ""))
    if not FORK_VERSION_PATTERN.fullmatch(observed_fork_version):
        raise CeremonyError("Deposit-data fork version is absent or invalid")
    if observed_fork_version.lower() != expected_fork_version:
        raise CeremonyError(
            "Deposit-data fork version does not match the committed NetworkProfile"
        )
    for field in (
        "withdrawal_credentials",
        "signature",
        "deposit_message_root",
        "deposit_data_root",
    ):
        if not isinstance(record.get(field), str) or not record[field]:
            raise CeremonyError(f"Deposit-data field {field!r} is absent")
    return keystore_path, deposit_path


def copy_to_clipboard(value: str) -> bool:
    pbcopy = shutil.which("pbcopy")
    if pbcopy is None:
        return False
    subprocess.run([pbcopy], input=value, text=True, check=True)
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one validator keystore and matching deposit-data file."
    )
    parser.add_argument("--validator-id", required=True)
    parser.add_argument("--network-profile", required=True)
    parser.add_argument(
        "--chain",
        help="Deposit CLI chain name; defaults to the profile without a numeric suffix.",
    )
    parser.add_argument("--deposit-cli", type=Path)
    parser.add_argument(
        "--tools-root",
        type=Path,
        default=Path.home() / ".local/share/eth-validator-platform/tools",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path.home() / ".local/share/eth-validator-platform",
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        default=ROOT / "applications/networks",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Do not copy the deposit-data path with pbcopy.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not NETWORK_PROFILE_PATTERN.fullmatch(args.network_profile):
        raise CeremonyError("Network profile has an invalid identifier")
    if not VALIDATOR_ID_PATTERN.fullmatch(args.validator_id):
        raise CeremonyError("Validator identity has an invalid identifier")
    if not args.validator_id.startswith(f"validator-{args.network_profile}-"):
        raise CeremonyError("Validator identity does not begin with the network profile")
    family, expected_fork_version = load_network_contract(
        args.network_profile, args.profile_root
    )
    chain = args.chain or family
    if not NETWORK_PROFILE_PATTERN.fullmatch(chain):
        raise CeremonyError("Deposit CLI chain has an invalid identifier")
    if chain != family:
        raise CeremonyError("Deposit CLI chain does not match the NetworkProfile family")

    executable = resolve_deposit_cli(args.deposit_cli, args.tools_root)
    target = prepare_target(args.output_root, args.network_profile, args.validator_id)
    run_deposit_cli(executable, target, chain)
    keystore_path, deposit_path = validate_artifacts(
        target, chain, expected_fork_version
    )

    print("Generated and validated one encrypted validator-keystore bundle.")
    print(f"Output directory: {display_path(target)}")
    print(f"Keystore: {display_path(keystore_path)}")
    print(f"Deposit data: {display_path(deposit_path)}")
    if not args.no_clipboard and copy_to_clipboard(str(deposit_path)):
        print("Copied the deposit-data file path to the clipboard.")
    elif not args.no_clipboard:
        print("pbcopy is unavailable; the deposit-data path was not copied.")
    print(
        "The wrapper did not repeat the mnemonic, password, withdrawal address, "
        "or public key."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except CeremonyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
