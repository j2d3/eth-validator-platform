# Security policy

Do not open a public issue containing credentials, validator public-key inventories tied to clients, infrastructure addresses, logs with sensitive metadata, or signing material.

The following must never enter Git, Terraform state, GitHub Actions secrets, or ordinary Kubernetes Secret manifests:

- validator or withdrawal private keys;
- validator mnemonics and keystore passwords;
- HSM/MPC recovery material;
- production slashing-protection exports;
- AWS long-lived access keys.

Use short-lived GitHub OIDC sessions for AWS. Use AWS Secrets Manager only for the Engine API JWT in this lab. Production validator signing keys belong behind a remote signer backed by HSM/MPC controls and a separately operated, highly available slashing-protection database.

For a suspected signing compromise, follow [signer isolation](docs/runbooks/signer-isolation.md) before attempting workload recovery.
