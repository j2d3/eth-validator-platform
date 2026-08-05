# Secrets and key projection

**Owner**: `terraform/environments/dev/signer-foundation.tf`,
`hack/onboard-web3signer-keystore.py`,
`platform/apps/dev/validator-keystore-secret.yaml`,
`platform/apps/base/web3signer/deployment.yaml`, and the External Secrets
Operator (ESO) installation under `platform/apps/base/external-secrets/`.

## The pipeline

```text
operator laptop            AWS Secrets Manager        EKS signing namespace       Web3Signer
─────────────────          ─────────────────────      ─────────────────────       ──────────
1. keygen (offline)   ─►
2. onboard tool       ─►   identity-addressed
                            containers (empty,
                            declared by Terraform)
                             │
                             │ 3. shared ExternalSecret
                             │    (refreshInterval: 15m,
                             │     fans in all identities)
                             ▼
                                                     4. one Kubernetes Secret
                                                        (all identities inside,
                                                        one file-triple per key)
                                                             │
                                                             │ 5. mounted under
                                                             │    /var/run/web3signer/keys
                                                             ▼
                                                                                 6. loads all
                                                                                    keystore .yaml
                                                                                    descriptors at
                                                                                    Pod start
```

## Where each surface owns what

- **Terraform** declares one empty Secrets Manager container per validator
  identity via `for_each` on `local.web3signer_signing_key_names`. It never
  writes secret material. Adding validator #N is a `1 add / 1 in-place update
  / 0 destroy` plan (the in-place update grows the scoped signing-reader IAM
  policy's `resources` list by iteration). Removals use
  `moved { from = ... to = ...[key] }` to preserve populated containers
  across state re-addressing (see PR #127).
- **Operator laptop** runs `hack/onboard-web3signer-keystore.py` from a
  trusted workstation. This tool is the only writer of secret material. It
  takes an EIP-2335 keystore + password, verifies scrypt parameters, checks
  the target container is empty (refuses overwrite), and writes a single
  JSON blob with two properties: `keystore` (the encrypted keystore JSON)
  and `password`.
- **External Secrets Operator** long-polls Secrets Manager. It materializes
  **one shared Kubernetes Secret** named `web3signer-validator-keystore` in
  the `signing` namespace (see `platform/apps/dev/validator-keystore-secret.yaml`).
- **Web3Signer Deployment** mounts that one Secret at
  `/var/run/web3signer/keys` and loads the file-keystore descriptor `.yaml`
  files it finds there. Its `keystoreDescriptors` inventory therefore grows
  by adding entries to the shared ExternalSecret template, not by adding
  new Secrets.

## The shared-ExternalSecret shape (as implemented today)

At the current head there is exactly one `ExternalSecret` in the signing
namespace:

- `metadata.name: web3signer-validator-keystore`
- `spec.refreshInterval: 15m`
- `spec.refreshPolicy`: unset (ESO default)
- `spec.target.name: web3signer-validator-keystore`, `creationPolicy: Owner`,
  `deletionPolicy: Retain`
- `spec.data[]` fans in **all** identity-addressed Secrets Manager
  containers under one ExternalSecret, one `secretKey` per identity's
  `keystore` and `password` property.
- `spec.target.template.data` emits three files per identity:
  `validator.json` + `validator.password` + `validator.yaml` for #01,
  `validator-02.*` for #02, and one triple per subsequent identity
  (#03, #04, and any that follow). The `.yaml` descriptor names the
  paired `.json` + `.password` files that Web3Signer's `file-keystore`
  loader reads.

Consequences of this shape:

- **Adding a validator is a multi-file change, not a single-file edit.**
  Terraform (`for_each` map), the ExternalSecret (`data[]` + `template.data`),
  and — for signing activation — the assignment/identity catalog all move
  together. This is why validator #3 required PRs #133 + #135 + #136
  rather than one PR (and validator #4 required the same shape via
  PRs #139 + #141 + #144).
- **Shared failure domain across identities.** If the shared ExternalSecret
  goes NotReady, the projected Secret is not updated for any identity. The
  running Web3Signer Pod continues on cached values until it restarts, so
  this is not immediately fatal, but it does mean all identities share
  a rotation/failure boundary. A per-identity ExternalSecret split is
  future design — worth doing when the fleet is much larger, but not
  yet justified at the current small key count.
- **`refreshInterval: 15m`** is the poll cadence, not the propagation
  guarantee. `refreshPolicy` is unset, so ESO's default behavior applies.

## Identity-addressed naming (Secrets Manager side)

Each Secrets Manager container is named
`eth-validator-platform-dev/signing/validator-keystore[-NN]`:

- `.../signing/validator-keystore` (validator #1, historical name preserved
  via a `moved` block in Terraform).
- `.../signing/validator-keystore-02`, `...-03`, `...-04` (subsequent
  identities).

The `for_each` map keys off the ValidatorIdentity's metadata name
(`validator-ephemery-162-01` etc.), so the AWS resource address is stable
even if the AWS-visible name preserves the pre-`for_each` form.

## The onboarding tool's boundary contract

`hack/onboard-web3signer-keystore.py` is deliberately not a Kubernetes
component. It runs on the operator's trusted workstation because:

- The keystore + password pass through operator memory only.
- The tool's AWS credentials are the operator's — SSO-issued,
  short-lived. No long-lived key ever touches the cluster or CI.
- The tool refuses to write if the container is non-empty. Overwriting an
  existing keystore requires an explicit separate flow.
- The tool validates scrypt parameters (`N=262144, r=8, p=1`), computes
  the pubkey from the encrypted secret, and refuses to write if the
  derived pubkey doesn't match the declared identity's `publicKey`.

CI never has credentials to write to Secrets Manager; the onboarding tool
has no in-cluster counterpart.

## What the slashing DB is *for*

Web3Signer's PostgreSQL slashing-protection database is the **authoritative
record of what this validator has already attested to or proposed**. On
every duty, Web3Signer records the message intent *before* signing. On any
subsequent duty with a conflicting attestation slot/target-epoch, or a
duplicate proposal slot, Web3Signer refuses to sign and emits a `prevented`
metric.

Why not rely on the validator client's local slashing store?

- **VC-local state is disposable** across Pod evictions, node restarts,
  chart upgrades, or client swaps.
- **Client-swap is a first-class operation.** Moving an identity from
  Lighthouse VC to Teku VC leaves the durable history intact in RDS. The
  two VC vendors don't share a local-slashing DB format; Web3Signer + RDS
  is the only common substrate.
- **Concurrent duty attempts across VC restarts.** During a VC restart
  window, an old Pod terminating and a new Pod starting can briefly both
  hold the same identity. Web3Signer + RDS serializes; local per-VC
  state does not.

The DB is written *before* the signature is returned. That ordering —
record intent, then sign — is what makes it authoritative.

## References

- Web3Signer implementation: [`web3signer-and-slashing-protection`](web3signer-and-slashing-protection.md)
- Terraform: [`terraform-aws-foundation`](terraform-aws-foundation.md)
- Boundaries: [architecture/safety-and-custody-boundaries](../architecture/safety-and-custody-boundaries.md)
- Onboarding tool: `hack/onboard-web3signer-keystore.py`
- Manifest: `platform/apps/dev/validator-keystore-secret.yaml`
- Related PRs: #127 (Terraform `for_each` + `moved`), #128/#135 (shared
  ExternalSecret extensions per new key), #133/#139 (empty-container
  declarations for #3/#4), #103–#108 (bootstrap chain)
