# Validator key ceremony

## Scope

This procedure creates one new EIP-2335 validator keystore and its matching
deposit-data file on the operator workstation. It does not deposit ETH, write
to AWS, project a key into EKS, or enable signing.

Use a new validator identity for every validator client. Never copy an existing
keystore to another assignment. Withdrawal credentials and the validator
signing key are separate: the withdrawal address belongs to the operator; the
BLS signing key is loaded by Web3Signer only after the deposit and activation
gates pass.

## Preconditions

- Use a trusted workstation and an offline-capable deposit CLI build whose
  provenance has been checked separately.
- Confirm the current `NetworkProfile` before generating or submitting a
  deposit. Ephemery is generation-addressed and an old wallet network or old
  deposit page can submit to the wrong chain.
- Terraform must already declare an empty identity-addressed Secrets Manager
  container for the planned validator ID. Key generation does not create that
  container.
- Keep all generated files outside the repository. The helper defaults to
  `~/.local/share/eth-validator-platform/`.

For an Ephemery identity, inspect the committed chain identity without printing
key material:

```bash
PATH="$PWD/.local/test-venv/bin:$PATH" python3 - <<'PY'
from pathlib import Path
import yaml

profile = yaml.safe_load(Path("applications/networks/ephemery-162.yaml").read_text())
identity = profile["spec"]["identity"]
print("profile:", profile["metadata"]["name"])
print("chain id:", identity["executionChainId"])
print("genesis fork version:", identity["genesisForkVersion"])
print("deposit contract:", profile["spec"]["deposit"]["contractAddress"])
PY
```

The wallet network used for the deposit must report the same chain ID. Do not
accept a deposit page's automatic network switch without comparing it to this
output.

## Generate one key

Run the wrapper with the planned catalog identity. This example is validator
05; change both identifiers together for another network or ordinal:

```bash
PATH="$PWD/.local/test-venv/bin:$PATH" python3 hack/generate-validator-key.py \
  --validator-id validator-ephemery-162-05 \
  --network-profile ephemery-162
```

The wrapper:

1. locates exactly one installed EthStaker deposit CLI, or requires an explicit
   `--deposit-cli` when several are installed;
2. creates one empty identity-addressed directory and refuses reuse;
3. lets the deposit CLI display the new mnemonic and prompt for the mnemonic
   language, keystore password, and withdrawal address without putting those
   values in shell history;
4. requires one private-mode EIP-2335 keystore and one matching 32-ETH
   deposit-data record whose network family and fork version match the
   committed `NetworkProfile`; and
5. copies only the deposit-data **file path** to the macOS clipboard.

The wrapper does not repeat the mnemonic, password, withdrawal address, or BLS
public key after the deposit CLI's interactive ceremony. The deposit CLI must
display the new mnemonic once so the operator can record it offline; do not
record the terminal session. If generation fails, the wrapper leaves the
isolated directory in place for inspection and a deliberate cleanup; rerunning
cannot silently overwrite it.

To select a particular executable or disable clipboard use:

```bash
PATH="$PWD/.local/test-venv/bin:$PATH" python3 hack/generate-validator-key.py \
  --validator-id validator-ephemery-162-05 \
  --network-profile ephemery-162 \
  --deposit-cli "$HOME/path/to/deposit" \
  --no-clipboard
```

## Submit and verify the deposit

Upload the generated deposit-data file to the launchpad for the exact network
generation and connect the disposable testnet execution wallet. Before signing
the transaction, verify all of the following in the wallet:

- the selected network chain ID matches the committed `NetworkProfile`;
- the withdrawal address is the intended operator-controlled address;
- the transaction target is the deposit contract in the profile; and
- the value is 32 test ETH for this regular validator.

Record the transaction hash and confirmation block in the private operator
record. A mined transaction is not sufficient when it reverted. Wait for a
successful receipt, then verify through the pair's beacon API or an independent
explorer that the matching validator appears with the intended balance and
withdrawal credentials. Do not commit a full validator public key or the
deposit-data payload as evidence.

## Store the encrypted keystore

Initialize Terraform from the actual environment root. Running `terraform
init` from the repository root initializes an empty directory and does not fix
the environment backend:

```bash
terraform -chdir=terraform/environments/dev init \
  -reconfigure \
  -backend-config=backend.hcl
```

Locate the one keystore inside this identity's directory and pass its planned
identity to the zero-file onboarding tool:

```bash
KEY_DIR="$HOME/.local/share/eth-validator-platform/ephemery-162/validator-ephemery-162-05"
KEYSTORE_FILE="$(find "$KEY_DIR" -type f -name 'keystore-*.json' -print -quit)"
test -n "$KEYSTORE_FILE"

python3 hack/onboard-web3signer-keystore.py \
  --keystore "$KEYSTORE_FILE" \
  --network-profile ephemery-162 \
  --validator-id validator-ephemery-162-05
```

The onboarding tool verifies the password and derived public key, selects the
exact Terraform-declared empty container, writes through AWS CLI standard
input, reads the stored JSON back for an exact comparison, and refuses an
existing secret version. It does not handle the mnemonic, deposit transaction,
or withdrawal credential.

If Terraform reports that backend initialization is required, rerun the exact
environment-root `init -reconfigure -backend-config=backend.hcl` command above.
Do not run a plain repository-root `terraform init`.

## Activation boundary

Key generation, a successful deposit, and secret onboarding still do not
authorize signing. A separate reviewed change must:

- add the identity to the shared ExternalSecret/Web3Signer projection;
- observe exactly the expected number of keys loaded;
- verify the beacon registry entry, balance, withdrawal credentials, and
  activation state;
- bind the disjoint identity to one synchronized client pair;
- preserve the shared RDS slashing-protection path; and
- observe doppelganger clearance before accepting a first duty.

See [Secrets and key projection](../components/secrets-and-key-projection.md)
for the AWS/EKS data flow and
[Web3Signer and slashing protection](../components/web3signer-and-slashing-protection.md)
for the signing boundary.
