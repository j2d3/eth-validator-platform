#!/usr/bin/env python3
"""Store one encrypted EIP-2335 validator keystore without exposing its password.

The deposit tool creates the encrypted keystore before this command runs. This
tool never handles a mnemonic, deposit transaction, or withdrawal credential.
It prompts for the keystore password without echo, refuses implicit rotation,
and streams the resulting JSON only through the AWS CLI's stdin.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = ROOT / "terraform" / "environments" / "dev"
NETWORK_PROFILE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
PUBLIC_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{96}$")
VALIDATOR_PATH_PATTERN = re.compile(r"^m/12381/3600/[0-9]+/0/0$")
MAX_KEYSTORE_BYTES = 64 * 1024


class OnboardingError(RuntimeError):
    """A bounded validation or external-command failure."""


def run(
    command: list[str],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or "command failed"
        raise OnboardingError(f"{command[0]} failed: {detail}") from exc


def terraform_output(name: str) -> Any:
    result = run(
        [
            "terraform",
            f"-chdir={TERRAFORM_ROOT}",
            "output",
            "-json",
            name,
        ]
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OnboardingError(f"Terraform output {name!r} was not JSON") from exc


def validate_keystore(path: Path) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OnboardingError(f"Cannot read the encrypted keystore: {exc}") from exc
    if not raw or len(raw) > MAX_KEYSTORE_BYTES:
        raise OnboardingError(
            f"Encrypted keystore must contain 1 through {MAX_KEYSTORE_BYTES} bytes"
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnboardingError("Encrypted keystore is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise OnboardingError("Encrypted keystore must be one JSON object")
    if value.get("version") != 4:
        raise OnboardingError("Encrypted keystore must use EIP-2335 version 4")
    try:
        uuid.UUID(str(value["uuid"]))
    except (KeyError, ValueError, AttributeError) as exc:
        raise OnboardingError("Encrypted keystore uuid is absent or invalid") from exc
    public_key = str(value.get("pubkey", "")).removeprefix("0x")
    if not PUBLIC_KEY_PATTERN.fullmatch(public_key):
        raise OnboardingError("Encrypted keystore pubkey must be one 48-byte BLS key")
    if not isinstance(value.get("path"), str) or not VALIDATOR_PATH_PATTERN.fullmatch(
        value["path"]
    ):
        raise OnboardingError("Encrypted keystore must declare an EIP-2334 validator path")
    crypto = value.get("crypto")
    if not isinstance(crypto, dict):
        raise OnboardingError("Encrypted keystore crypto object is absent")
    for component in ("kdf", "checksum", "cipher"):
        item = crypto.get(component)
        if not isinstance(item, dict) or not isinstance(item.get("function"), str):
            raise OnboardingError(
                f"Encrypted keystore crypto.{component} is absent or invalid"
            )
    canonical = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return canonical, f"0x{public_key.lower()}"


def verify_keystore_password(canonical_keystore: str, password: str) -> None:
    """Verify the EIP-2335 checksum without decrypting or writing key material."""

    crypto = json.loads(canonical_keystore)["crypto"]
    kdf = crypto["kdf"]
    params = kdf.get("params")
    if not isinstance(params, dict):
        raise OnboardingError("Encrypted keystore KDF parameters are invalid")
    try:
        salt = bytes.fromhex(params["salt"])
        dklen = int(params["dklen"])
        cipher_message = bytes.fromhex(crypto["cipher"]["message"])
        expected_checksum = bytes.fromhex(crypto["checksum"]["message"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OnboardingError("Encrypted keystore cryptographic fields are invalid") from exc
    if dklen != 32 or len(expected_checksum) != 32:
        raise OnboardingError("Encrypted keystore uses an unsupported key length")

    password_bytes = password.encode("utf-8")
    function = kdf["function"].lower()
    try:
        if function == "pbkdf2":
            if str(params.get("prf", "")).lower() != "hmac-sha256":
                raise OnboardingError("Encrypted keystore PBKDF2 PRF is unsupported")
            derived_key = hashlib.pbkdf2_hmac(
                "sha256", password_bytes, salt, int(params["c"]), dklen
            )
        elif function == "scrypt":
            derived_key = hashlib.scrypt(
                password_bytes,
                salt=salt,
                n=int(params["n"]),
                r=int(params["r"]),
                p=int(params["p"]),
                dklen=dklen,
            )
        else:
            raise OnboardingError(f"Encrypted keystore KDF {function!r} is unsupported")
    except (KeyError, TypeError, ValueError) as exc:
        raise OnboardingError("Encrypted keystore KDF parameters are invalid") from exc

    observed_checksum = hashlib.sha256(derived_key[16:32] + cipher_message).digest()
    if not hmac.compare_digest(observed_checksum, expected_checksum):
        raise OnboardingError("Keystore password does not match the encrypted keystore")


def prompt_password() -> str:
    first = getpass.getpass("Encrypted validator keystore password: ")
    second = getpass.getpass("Confirm keystore password: ")
    if first != second:
        raise OnboardingError("Keystore passwords did not match")
    if len(first) < 8:
        raise OnboardingError("Keystore password must contain at least eight characters")
    if any(ord(character) < 32 for character in first):
        raise OnboardingError("Keystore password contains a control character")
    return first


def existing_version_count(secret_id: str) -> int:
    result = run(
        [
            "aws",
            "secretsmanager",
            "list-secret-version-ids",
            "--secret-id",
            secret_id,
            "--include-deprecated",
            "--query",
            "length(Versions)",
            "--output",
            "text",
        ]
    )
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise OnboardingError("AWS returned an invalid secret-version count") from exc


def store_secret(secret_id: str, payload: dict[str, str]) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    run(
        [
            "aws",
            "secretsmanager",
            "put-secret-value",
            "--secret-id",
            secret_id,
            "--secret-string",
            "file:///dev/stdin",
            "--output",
            "json",
        ],
        input_text=encoded,
    )
    observed = run(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_id,
            "--query",
            "SecretString",
            "--output",
            "text",
        ]
    ).stdout
    try:
        observed_payload = json.loads(observed)
    except json.JSONDecodeError as exc:
        raise OnboardingError("Stored secret readback was not JSON") from exc
    if observed_payload != payload:
        raise OnboardingError("Stored secret readback did not match the in-memory payload")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Store one encrypted validator keystore for Web3Signer."
    )
    parser.add_argument(
        "--keystore",
        required=True,
        type=Path,
        help="Path to one encrypted EIP-2335 keystore JSON file.",
    )
    parser.add_argument(
        "--network-profile",
        required=True,
        help="Generation-addressed NetworkProfile, for example ephemery-162.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not NETWORK_PROFILE_PATTERN.fullmatch(args.network_profile):
        raise OnboardingError("Network profile is not a valid repository identifier")
    keystore, public_key = validate_keystore(args.keystore)
    password = prompt_password()
    verify_keystore_password(keystore, password)

    outputs = terraform_output("web3signer_secret_arns")
    if not isinstance(outputs, dict) or not isinstance(
        outputs.get("signing_key_bundle"), str
    ):
        raise OnboardingError(
            "Terraform output web3signer_secret_arns.signing_key_bundle is absent"
        )
    secret_id = outputs["signing_key_bundle"]
    versions = existing_version_count(secret_id)
    if versions != 0:
        raise OnboardingError(
            "Signing-key container already has a version; explicit rotation is required"
        )

    store_secret(
        secret_id,
        {
            "format": "eip2335-file-keystore-v1",
            "keystore": keystore,
            "networkProfile": args.network_profile,
            "password": password,
            "publicKey": public_key,
        },
    )
    print(
        "Stored one encrypted Web3Signer keystore with exact readback; "
        f"public key: {public_key}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except OnboardingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
