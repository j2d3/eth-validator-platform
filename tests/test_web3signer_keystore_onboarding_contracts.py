"""Contracts for restricted Web3Signer validator-keystore onboarding."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hack" / "onboard-web3signer-keystore.py"


def load_module():
    spec = importlib.util.spec_from_file_location("web3signer_keystore_onboarding", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load onboarding tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_keystore(password: str = "correct horse") -> dict:
    salt = bytes.fromhex("11" * 32)
    cipher_message = bytes.fromhex("ef" * 32)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 2, 32
    )
    checksum = hashlib.sha256(derived_key[16:32] + cipher_message).hexdigest()
    return {
        "version": 4,
        "uuid": "65ae7e31-e63d-4adf-9e84-03c6bec6c69f",
        "path": "m/12381/3600/0/0/0",
        "pubkey": "ab" * 48,
        "crypto": {
            "kdf": {
                "function": "pbkdf2",
                "params": {
                    "dklen": 32,
                    "c": 2,
                    "prf": "hmac-sha256",
                    "salt": salt.hex(),
                },
                "message": "",
            },
            "checksum": {"function": "sha256", "params": {}, "message": checksum},
            "cipher": {"function": "aes-128-ctr", "params": {}, "message": "ef" * 32},
        },
    }


def valid_scrypt_keystore(password: str = "correct horse") -> dict:
    value = valid_keystore(password)
    salt = bytes.fromhex("22" * 32)
    cipher_message = bytes.fromhex(value["crypto"]["cipher"]["message"])
    params = {"dklen": 32, "n": 16, "r": 8, "p": 1, "salt": salt.hex()}
    derived_key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=16, r=8, p=1, dklen=32
    )
    value["crypto"]["kdf"] = {
        "function": "scrypt",
        "params": params,
        "message": "",
    }
    value["crypto"]["checksum"]["message"] = hashlib.sha256(
        derived_key[16:32] + cipher_message
    ).hexdigest()
    return value


class Web3SignerKeystoreOnboardingContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_accepts_one_encrypted_eip2335_validator_keystore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keystore.json"
            path.write_text(json.dumps(valid_keystore()), encoding="utf-8")
            canonical, public_key = self.module.validate_keystore(path)

        self.assertEqual(json.loads(canonical), valid_keystore())
        self.assertEqual(public_key, "0x" + "ab" * 48)
        self.assertNotIn("private", canonical.lower())
        self.assertNotIn("mnemonic", canonical.lower())
        self.module.verify_keystore_password(canonical, "correct horse")

        with self.assertRaisesRegex(self.module.OnboardingError, "does not match"):
            self.module.verify_keystore_password(canonical, "wrong battery")

    def test_rejects_wrong_version_path_pubkey_and_crypto_shape(self) -> None:
        mutations = (
            ("version", lambda value: value.update(version=3)),
            ("path", lambda value: value.update(path="m/44/0/0")),
            ("partial-path", lambda value: value.update(path="m/12381/3600")),
            ("pubkey", lambda value: value.update(pubkey="ab")),
            ("crypto", lambda value: value.update(crypto={})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                value = valid_keystore()
                mutate(value)
                path = Path(directory) / "keystore.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(self.module.OnboardingError):
                    self.module.validate_keystore(path)

    def test_password_prompt_requires_matching_nontrivial_values(self) -> None:
        with mock.patch.object(
            self.module.getpass, "getpass", side_effect=["correct horse", "correct horse"]
        ):
            self.assertEqual(self.module.prompt_password(), "correct horse")
        with mock.patch.object(
            self.module.getpass, "getpass", side_effect=["correct horse", "wrong battery"]
        ):
            with self.assertRaisesRegex(self.module.OnboardingError, "did not match"):
                self.module.prompt_password()

    def test_scrypt_password_verification_raises_the_bounded_memory_ceiling(self) -> None:
        canonical = json.dumps(valid_scrypt_keystore(), separators=(",", ":"))
        self.module.verify_keystore_password(canonical, "correct horse")

        production_n = 262144
        required = 128 * production_n * 8 + 128 * 8
        configured = required + self.module.SCRYPT_MEMORY_HEADROOM
        self.assertGreater(configured, 256 * 1024 * 1024)
        self.assertLessEqual(configured, self.module.MAX_SCRYPT_MEMORY)

        value = valid_scrypt_keystore()
        value["crypto"]["kdf"]["params"]["n"] = 3
        with self.assertRaisesRegex(self.module.OnboardingError, "N is unsupported"):
            self.module.verify_keystore_password(json.dumps(value), "correct horse")

    def test_secret_value_uses_stdin_and_never_argv(self) -> None:
        payload = {
            "format": "eip2335-file-keystore-v1",
            "keystore": json.dumps(valid_keystore()),
            "networkProfile": "ephemery-162",
            "password": "not-on-the-command-line",
            "publicKey": "0x" + "ab" * 48,
        }
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(command: list[str], *, input_text: str | None = None):
            calls.append((command, input_text))
            stdout = json.dumps(payload) if "get-secret-value" in command else "{}"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        with mock.patch.object(self.module, "run", side_effect=fake_run):
            self.module.store_secret("secret-arn", payload)

        put_command, put_stdin = calls[0]
        self.assertIn("--secret-string", put_command)
        self.assertIn("file:///dev/stdin", put_command)
        self.assertNotIn(payload["password"], "\0".join(put_command))
        self.assertEqual(json.loads(put_stdin or ""), payload)
        for command, _ in calls:
            self.assertNotIn(payload["password"], "\0".join(command))

    def test_existing_secret_version_refuses_implicit_rotation(self) -> None:
        result = subprocess.CompletedProcess(
            ["aws"], 0, stdout="1\n", stderr=""
        )
        with mock.patch.object(self.module, "run", return_value=result):
            self.assertEqual(self.module.existing_version_count("secret-arn"), 1)

        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("explicit rotation is required", source)
        self.assertNotIn("batch-delete-secret", source)
        self.assertNotIn("delete-secret", source)

    def test_tool_never_writes_secret_material_to_a_local_file(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            ".write_text(",
            ".write_bytes(",
            "NamedTemporaryFile",
            "mkstemp",
            "shell=True",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
