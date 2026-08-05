# Safety and custody boundaries

Where the sensitive data lives, what may cross each boundary, and what
enforces the crossing rules. Durable — this page should still be correct
after multiple client-pair additions.

## The bounded custody claim

**The platform holds encrypted signing keystores. It does not hold, and
never receives, withdrawal credentials.** A slashing incident here can
lose a customer's stake; it cannot move a customer's principal.

That distinction is why controls are custody-grade despite the platform
never being a fund custodian.

## What lives where

| Material | Location | Boundary enforcement |
|---|---|---|
| Withdrawal mnemonic | Offline operator custody | Never imported — no platform surface accepts it |
| Withdrawal private key | Offline operator custody | Same |
| Validator private key | Encrypted inside the EIP-2335 keystore in AWS Secrets Manager | Password required to decrypt; only Web3Signer holds the password + keystore together at rest |
| EIP-2335 keystore JSON | AWS Secrets Manager (one identity-addressed container per validator) | Terraform declares empty containers via `for_each`; only `hack/onboard-web3signer-keystore.py` writes into them; scoped IAM policy enumerates the exact ARNs |
| Keystore password | Same AWS Secrets Manager container as the keystore | Same as above |
| Slashing-protection history | RDS PostgreSQL (Web3Signer's `slashing_protection` schema) | Single-AZ TLS `verify-full`, Web3Signer is the only writer, migration Job runs before Web3Signer is admitted |
| Public validator key | Git (in `applications/validators/identities/`) | Public data by construction |
| Fee recipient | Git (in the assignment) | Public data |
| Chart image digests | Git (in `charts/ethereum-node/values.yaml`) | Public data; digests are supply-chain provenance, not secrets |
| Engine JWT | AWS Secrets Manager, projected per-pair via ExternalSecret | Per-pair scope via `fullnameOverride`; never touches shared signer namespace |
| RDS master password | AWS-managed rotation secret | Bootstrap tool reads it only into process memory, never disk |

## The two independent non-signing enforcement layers

Signing cannot happen on this platform unless **both** of the following
succeed at chart render time:

1. **Chart schema gate**:
   `.Values.networkProfile.signer.web3signer.signingQualified == true` is
   required for any `validator.enabled=true` render. Set on the
   NetworkProfile after the operator manually confirms the signer binding.
2. **Projection tool refusal for synthetic identities**: the
   `tools/render_local_assignments.py` projection tool rejects any
   assignment whose `ValidatorIdentity.synthetic=true` when
   `signingEnabled=true`. Only registered identities with a public key +
   `signingSecretRef` can project into a signing HelmRelease.

Additionally, the assignment itself requires:

- `signingEnabled: true` (default is `false`).
- `feeRecipient` populated (JSON Schema required-if-signing).
- `safety.slashingProtectionConfirmed: true`.
- `safety.doppelgangerProtectionConfirmed: true`.

None of these are default-on. Every enable is a human review moment.

## The Kubernetes-runtime enforcement

Independent of chart rendering:

- **Web3Signer NetworkPolicy** limits egress to DNS + RDS only. The signer
  cannot reach the internet or the beacon P2P mesh (this caught the Teku
  Ephemery-preset bootnode-fetch attempt in
  [#115](https://github.com/j2d3/eth-validator-platform/pull/115)).
- **Signer key-store volume is `readOnly: true`** with `defaultMode: 0o440`
  and `fsGroup: 999` — the signer process can read the projected keystore
  files, nothing else can, and the container cannot write into the mount.
- **Validator client uses `--disable-slashing-protection-web3signer`
  (Lighthouse) or `--validators-external-signer-slashing-protection-enabled=false`
  (Teku)** — the VC's local slashing bookkeeping is not authoritative;
  Web3Signer + RDS is.
- **Validator client uses `--enable-doppelganger-protection` (Lighthouse)
  or `--doppelganger-detection-enabled=true` (Teku)** — refuses to sign
  for ~2 epochs after startup while listening for duplicates.
- **Distinct-keys invariant enforced at CI**: the
  `test_signing_node_layer_waits_for_signer_application` assertion
  requires `len(signing_public_keys) == N` for N signing releases. Two
  releases can never share a public key.

## The audit trail

- **Git**: every change to policy, catalog, chart, IAM, or network policy
  is a reviewed commit with a paired agent approval at the exact head.
- **AWS CloudTrail**: every Secrets Manager `GetSecretValue`, KMS decrypt,
  and IAM role assumption is logged.
- **Web3Signer metrics**: `permitted` / `prevented` / `missing_identifier`
  counters are exposed and dashboarded. Any non-zero `prevented` is a
  safety-signal investigation, not throughput.
- **Beacon-chain public record**: every attestation and proposal has a
  public slot, committee, validator index, and result. Attributability is
  cryptographic.

## What the boundaries do not cover

Explicitly out of scope, called out here so no one assumes otherwise:

- **AWS account takeover recovery**: if the human's `j2d3` AWS credentials
  are compromised, the attacker can rotate IAM, delete RDS, or read the
  Secrets Manager values. No HSM or hardware-anchored root of trust exists
  in this lab.
- **Mainnet withdrawal safety**: withdrawal credentials are offline in
  operator custody, but no formal ceremony is documented for withdrawing
  from mainnet — the platform doesn't touch mainnet.
- **Long-term slashing-DB recovery**: point-in-time restore, cross-region
  replication, and slashing-history import from an exported file are all
  unqualified. RDS is Single-AZ. The lab is deliberately not sized for
  disaster-recovery drills.

Details in
[`components/web3signer-and-slashing-protection.md`](../components/web3signer-and-slashing-protection.md)
and the PRD §5 safety invariants.
