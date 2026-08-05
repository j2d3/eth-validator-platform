# Desired-state catalog

**Owner**: `applications/` tree + `schemas/` + `tools/render_local_assignments.py`.

The catalog is a relational, schema-validated source of truth for who is
running what. `tools/render_local_assignments.py` projects it into Flux
`HelmRelease` manifests under `platform/apps/local/assignments/`; those
projections are committed and drift-checked in CI.

## Kinds

Each kind has a JSON Schema in `schemas/` and one YAML file per instance
in `applications/`:

- **Customer** (`applications/customers/`) — business tenancy.
- **NetworkProfile** (`applications/networks/`) — chain identity, artifact
  bundle, signer binding. Immutable identity fields; changing them requires
  a new profile file (generation-pinned).
- **ServiceProfile** (`applications/profiles/`) — `dedicated-<EL>-<CL>`
  naming; declares tenancy, resource profile, and `signingAllowed` at the
  profile level.
- **ValidatorIdentity** (`applications/validators/identities/`) — either
  synthetic (draft, no public key, cannot sign) or registered (has
  `publicKey` + `signingSecretRef`).
- **ValidatorAssignment** (`applications/validators/assignments/`) — ties
  an identity to a service profile on a network with a lifecycle
  (`stopped` / `active` / `archived`) and safety acknowledgments.

## The projection tool as a load-bearing validator

`tools/render_local_assignments.py` refuses to project any assignment that
cannot resolve cleanly:

- Unknown `validatorRef` / `serviceProfileRef` / `networkProfileRef`.
- Unimplemented client adapter (against `SUPPORTED_EXECUTION_CLIENTS` /
  `SUPPORTED_CONSENSUS_CLIENTS` constants).
- `signingEnabled: true` on a `synthetic: true` identity.
- Signing without: `publicKey`, `signingSecretRef`, `feeRecipient`, both
  safety flags true, and `networkProfile.signer.web3signer.signingQualified: true`.
- Any EL adapter and CL adapter with mismatched `mode`.

CI runs `python3 tools/render_local_assignments.py --check` and fails on
any drift between committed projections and re-derived output.

## Adding a new client pair

Follow the pattern established for Reth, Teku, Erigon (see
[`docs/client-pairs/`](../client-pairs/)). Each pair is typically two
sequential PRs:

1. **Chart adapter PR**: adds `executionClients.<name>` or
   `consensusClients.<name>` to `charts/ethereum-node/values.yaml`,
   `values.schema.json`, dispatcher branches in `_helpers.tpl`, and
   contract tests.
2. **Catalog PR**: adds ServiceProfile + ValidatorIdentity + assignment;
   extends the projection tool's SUPPORTED set; extends the network
   profile's `clients` map; extends the EKS overlay patches; extends
   `test_signing_node_layer_waits_for_signer_application`.

See [`docs/client-pairs/README.md`](../client-pairs/README.md) for the
per-pair definition-of-done checklist.

## References

- Schemas: `schemas/`
- Projection tool: `tools/render_local_assignments.py`
- Related runbook: [`network-profiles`](../runbooks/network-profiles.md)
