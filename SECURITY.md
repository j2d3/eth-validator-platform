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

1. Isolate every caller of the signer for affected identities;
   confirm no residual signing capability remains (empty keystore,
   database access revoked, NetworkPolicy tightened).
2. Revoke and rotate the compromised service credentials — IAM roles,
   database users/passwords, TLS material, tokens.
3. Preserve slashing-protection history **complete through the last
   possibly signed duty** — do not restore a snapshot merely because
   it predates the incident window; duties signed during the window
   must be represented in the restored history or reactivation risks
   double-signing.
4. Prove single-writer status and conflicting-duty rejection with a
   deliberate slashable-request test (which must be rejected) before
   reactivation.
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
4. Follow the offline key-custody team's voluntary-exit / replacement
   procedure. Voluntary exit is an on-chain, irreversible operation
   requiring the withdrawal-credential holder's approval and its own
   safety review.
5. If a replacement identity is provisioned, treat it as a brand-new
   registration through the standard onboarding path — never as a
   continuation of the compromised one.

### Runbook status

A dedicated compromise-response runbook with cluster-specific commands
and evidence templates is not yet written; when it exists it will live
under `docs/runbooks/`. Until then, the outlines above are the
operator's procedure, and any deviation must be reviewed with an
approver who understands the BLS-key non-rotatability constraint.
