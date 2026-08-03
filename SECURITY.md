# Security policy

## Reporting a vulnerability

**Do not open a public issue** containing credentials, validator public-key
inventories tied to clients, infrastructure addresses, logs with sensitive
metadata, or signing material.

For sensitive disclosures, contact the maintainer through their
[GitHub profile](https://github.com/j2d3) via any GitHub-native private
channel available to you at the time of reporting. If no private channel is
available, open a public issue with a short redacted summary and request a
private-channel handoff before including any sensitive detail.

Because this is an educational lab there are no supported versions and no
service-level commitment on response times. Reports about patterns that
could compromise a downstream deployment — a subtle flaw in
`hack/merge-pr.sh`, a missing safety invariant in a chart, a schema that
permits an unsafe combination, a fail-open condition in the reconciliation
chain — are especially welcome.

## What must never appear in this repository

The following must never enter Git, Terraform state, GitHub Actions secrets,
or ordinary Kubernetes Secret manifests:

- validator or withdrawal private keys;
- validator mnemonics and keystore passwords;
- HSM/MPC recovery material;
- production slashing-protection exports;
- AWS long-lived access keys.

AWS authentication in this lab is performed from a trusted operator
workstation using the operator's own short-lived credentials; there is no
long-lived AWS access key in Git, Terraform state, or GitHub Actions
secrets, and GitHub Actions holds no AWS mutation authority. AWS Secrets
Manager is the designed source of environment secrets when the platform
reaches EKS — Engine API JWT, database credentials, and encrypted signing
keystores — each projected into the cluster through External Secrets
Operator on its own least-privilege path. Production validator signing
keys belong behind a remote signer backed by HSM/MPC controls and a
separately operated, highly available slashing-protection database.

## Signing compromise

A validator BLS signing key is **not** a rotatable service credential.
If it may have been disclosed, the identity must remain permanently
disabled and follow a controlled voluntary-exit / replacement
procedure — running the same validator identity under a new key is
not possible. Any response procedure that conflates a leaked BLS
signing key with a rotatable service credential is unsafe.

The two compromise cases below therefore require different responses.
Both apply to any signing path (remote signer, slashing-protection
database, IAM/network path to either) and require signing to remain
disabled until every step below is complete.

### Case A — service-access compromise; validator key proven not disclosed

The BLS signing keystore itself remains under approved custody and its
material was not exfiltrated. Something in the *access path* was
compromised: remote-signer credentials, database credentials, IAM
role, TLS material, service account, or network trust.

1. Isolate every caller of the signer for affected identities.
   Local controls can prove *this cluster's* signing paths are
   disabled — empty keystore, database access revoked, NetworkPolicy
   tightened — but cannot prove an incident actor has no copied
   credential outside the cluster; treat that as a separate custody
   question, not a claim you can close from the operator's console.
2. Revoke and rotate the compromised service credentials — IAM roles,
   database users/passwords, TLS material, tokens.
3. Preserve slashing-protection history **complete through the last
   possibly signed duty** — do not restore a snapshot merely because
   it predates the incident window; duties signed during the window
   must be represented in the restored history or reactivation risks
   double-signing.
4. Before reactivation, prove single-writer status and conflicting-duty
   rejection against a **hermetic clone** of the slashing-protection
   database, driven by a **synthetic unfunded validator key with no
   on-chain identity**. No beacon or validator client may be attached
   to the clone, no signature produced may reach a broadcast path, and
   the test key must never have been registered with any deposit
   contract. Never issue a deliberate slashable request against a
   funded or affected live identity — if any layer of the protection
   under test were to fail, that request would itself become the
   forbidden signature.
5. Restricted reactivation only after every step above is evidenced.

### Case B — validator BLS signing key possibly disclosed

The keystore material itself may have been exfiltrated. Recovery is
identity-level, not service-level.

1. Never reactivate the affected identity under the disclosed key.
2. Do not attempt to "rotate" the BLS signing key; there is no such
   operation. Signing under a different key would be a different
   validator identity and the on-chain identity remains bound to the
   original public key.
3. Preserve the identity's slashing-protection history and audit
   record — the exited identity's history must remain durable so a
   future forensic review can distinguish operator duties from
   any adversary-signed ones.
4. Trigger a voluntary exit through the safer of the two
   protocol-level exit mechanisms available for the identity, then
   provision a replacement per the offline key-custody team's
   procedure:
   - A consensus-layer **voluntary exit ([EIP-7044])** is signed by
     the validator's own active BLS key. When the concern is that
     that key has been disclosed, do **not** use the disclosed key
     to sign the exit unless the offline key-custody team confirms
     no safer path exists — signing with a possibly-adversary-held
     key is the failure mode this case exists to avoid.
   - An **execution-layer withdrawal-credential-triggered exit
     ([EIP-7002])** is the intended path when the BLS signing key is
     unavailable or untrusted: the exit is initiated by the
     withdrawal-credential holder acting through the execution layer
     against the beacon deposit contract, requiring no signature
     from the possibly-compromised BLS key. This is the safer path
     for a disclosed-key scenario when the identity's withdrawal
     credentials support it (`0x01`/`0x02`-style eligible
     credentials).
   - **Policy layer**, separate from the protocol: this project
     additionally requires the withdrawal-credential holder's
     approval before either exit mechanism is initiated. That is a
     custody-and-authorization policy of this lab, not a consensus
     requirement, and it applies on top of whichever protocol path is
     chosen.
5. If a replacement identity is provisioned, treat it as a brand-new
   registration through the standard onboarding path — never as a
   continuation of the compromised one.

[EIP-7002]: https://eips.ethereum.org/EIPS/eip-7002
[EIP-7044]: https://eips.ethereum.org/EIPS/eip-7044

### Runbook status

A dedicated compromise-response runbook with cluster-specific commands
and evidence templates is not yet written; when it exists it will live
under `docs/runbooks/`. Until then, the outlines above are the
operator's procedure, and any deviation must be reviewed with an
approver who understands the BLS-key non-rotatability constraint.
