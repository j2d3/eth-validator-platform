"""Contracts for the interactive validator-key ceremony wrapper."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hack" / "generate-validator-key.py"


def load_module():
    spec = importlib.util.spec_from_file_location("validator_key_ceremony", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load validator-key ceremony tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_fake_deposit_cli(
    path: Path,
    *,
    network: str = "ephemery",
    fork_version: str = "1000101b",
) -> None:
    source = f'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
folder = Path(args[args.index("--folder") + 1]) / "validator_keys"
chain = args[args.index("--chain") + 1]
if chain != {network!r}:
    raise SystemExit(3)
folder.mkdir(parents=True)
pubkey = "ab" * 48
keystore = {{
    "version": 4,
    "uuid": "65ae7e31-e63d-4adf-9e84-03c6bec6c69f",
    "path": "m/12381/3600/0/0/0",
    "pubkey": pubkey,
    "crypto": {{"kdf": {{"function": "scrypt"}}}},
}}
deposit = [{{
    "pubkey": pubkey,
    "withdrawal_credentials": "11" * 32,
    "amount": 32000000000,
    "signature": "22" * 96,
    "deposit_message_root": "33" * 32,
    "deposit_data_root": "44" * 32,
    "fork_version": {fork_version!r},
    "network_name": chain,
    "deposit_cli_version": "test",
}}]
for name, value in (
    ("keystore-test.json", keystore),
    ("deposit_data-test.json", deposit),
):
    artifact = folder / name
    artifact.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(artifact, 0o400)
'''
    path.write_text(source, encoding="utf-8")
    path.chmod(0o700)


class ValidatorKeyCeremonyContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_generation_uses_one_isolated_directory_and_validates_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_cli = root / "deposit"
            output_root = root / "output"
            write_fake_deposit_cli(fake_cli)

            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--validator-id",
                    "validator-ephemery-162-05",
                    "--network-profile",
                    "ephemery-162",
                    "--deposit-cli",
                    str(fake_cli),
                    "--output-root",
                    str(output_root),
                    "--no-clipboard",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            target = output_root / "ephemery-162/validator-ephemery-162-05"
            keystores = list(target.rglob("keystore-*.json"))
            deposits = list(target.rglob("deposit_data-*.json"))
            self.assertEqual(len(keystores), 1)
            self.assertEqual(len(deposits), 1)
            self.assertEqual(json.loads(deposits[0].read_text())[0]["amount"], 32_000_000_000)
            self.assertIn("Generated and validated one", result.stdout)
            self.assertNotIn("ab" * 48, result.stdout)
            self.assertNotIn("withdrawal_credentials", result.stdout)

    def test_refuses_a_nonempty_identity_directory_before_key_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            target = output_root / "ephemery-162/validator-ephemery-162-05"
            target.mkdir(parents=True)
            (target / "existing").write_text("do not replace", encoding="utf-8")
            fake_cli = root / "deposit"
            write_fake_deposit_cli(fake_cli)

            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--validator-id",
                    "validator-ephemery-162-05",
                    "--network-profile",
                    "ephemery-162",
                    "--deposit-cli",
                    str(fake_cli),
                    "--output-root",
                    str(output_root),
                    "--no-clipboard",
                ],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not empty", result.stderr)
            self.assertEqual((target / "existing").read_text(), "do not replace")

    def test_chain_defaults_from_a_generation_addressed_profile(self) -> None:
        self.assertEqual(self.module.default_chain("ephemery-162"), "ephemery")
        self.assertEqual(self.module.default_chain("hoodi"), "hoodi")
        self.assertEqual(
            self.module.load_network_contract(
                "ephemery-162", ROOT / "applications/networks"
            ),
            ("ephemery", "1000101b"),
        )

    def test_validator_identity_must_begin_with_the_exact_network_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_cli = root / "deposit"
            write_fake_deposit_cli(fake_cli)
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--validator-id",
                    "validator-prefix-ephemery-162-05",
                    "--network-profile",
                    "ephemery-162",
                    "--deposit-cli",
                    str(fake_cli),
                    "--output-root",
                    str(root / "output"),
                    "--no-clipboard",
                ],
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not begin", result.stderr)

    def test_generated_fork_version_must_match_the_committed_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_cli = root / "deposit"
            write_fake_deposit_cli(fake_cli, fork_version="ffffffff")
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--validator-id",
                    "validator-ephemery-162-05",
                    "--network-profile",
                    "ephemery-162",
                    "--deposit-cli",
                    str(fake_cli),
                    "--output-root",
                    str(root / "output"),
                    "--no-clipboard",
                ],
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the committed NetworkProfile", result.stderr)

    def test_auto_discovery_refuses_zero_or_multiple_executables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools_root = Path(directory)
            with self.assertRaisesRegex(self.module.CeremonyError, "not found"):
                self.module.resolve_deposit_cli(None, tools_root)

            for version in ("1.0.0", "2.0.0"):
                executable = (
                    tools_root
                    / f"ethstaker-deposit-cli-v{version}/bin/build-{version}/deposit"
                )
                executable.parent.mkdir(parents=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o700)
            with self.assertRaisesRegex(self.module.CeremonyError, "Multiple"):
                self.module.resolve_deposit_cli(None, tools_root)

    def test_password_withdrawal_address_and_mnemonic_never_enter_wrapper_argv(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "--keystore_password",
            "--withdrawal_address",
            "--execution_address",
            "--eth1_withdrawal_address",
            "--non_interactive",
            "existing-mnemonic",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("subprocess.run(command, check=True)", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
