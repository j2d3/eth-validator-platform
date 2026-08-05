# Web3Signer and slashing protection

**Owner**: `platform/apps/base/web3signer/` + `platform/apps/prerequisites/dev/`
+ `hack/bootstrap-web3signer-database.py` + the RDS instance defined in
`terraform/environments/dev/signer-foundation.tf`.

The signing tier is the safety-critical heart of the platform. This page
documents the design choices and their tradeoffs.

## What Web3Signer does here

Web3Signer is a **remote-signer service**. Validator clients (both
Lighthouse VC and Teku VC) do not hold private keys; they send unsigned
messages to Web3Signer and get back signed messages, provided Web3Signer
believes the signature is safe.

Choosing a remote signer over locally-mounted keystores gives us:

- **One durable slashing-protection database across validator-client
  restarts, client swaps, and Pod evictions.** A VC's local SQLite
  slashing history is disposable in a container-orchestrated environment;
  Web3Signer + RDS is not.
- **A single choke point for key material.** Only one Deployment ever
  handles decrypted keystores. The validator client cannot leak what it
  never held.
- **Support for validator-client heterogeneity.** Lighthouse VC and Teku
  VC talk the same remote-signer HTTP API to the same shared signer, so
  adding a third VC vendor doesn't fork the key-custody path.
- **Support for validator-count growth.** Adding a validator adds one
  identity-addressed AWS Secrets Manager container, one ExternalSecret
  data entry, and one file-keystore descriptor. Web3Signer scales
  vertically; the shared-signer/many-VCs shape avoids per-validator
  signer-tier duplication until fleet size demands sharding (PRD §15.1).

The trade is a shared-fate cross-validator dependency: if Web3Signer or
RDS is down, no validator on this cluster can sign. Mitigations below.

## What RDS provides

Web3Signer's slashing-protection history lives in a PostgreSQL database
(RDS `db.t4g.micro`, Single-AZ, TLS `verify-full`, encrypted at rest).
The schema is Web3Signer's own `slashing_protection` schema, applied by
a Flyway migration Job that runs before Web3Signer is ever admitted.

Key implementation details:

- **PostgreSQL, not the file-based alternative**: Web3Signer supports
  either, but the file-based option is single-writer and requires every
  VC to talk to the same host. PostgreSQL decouples durability and
  concurrency from local Pod state.
- **Single-AZ deployment (intentional for the lab, unacceptable for
  production)**: Multi-AZ would double cost and add ~1-2 minutes of
  failover time that this testnet lab doesn't need. Production
  replacement would flip to Multi-AZ with auto-failover.
- **TLS `verify-full` with an explicit RDS regional CA**: the pinned
  Flyway and Web3Signer images don't ship the AWS RDS regional root
  CA. The `platform/apps/base/aws-rds-ca/` overlay mounts the exact
  `us-west-2-rds-ca-rsa2048-g1.pem` root as a ConfigMap; both clients
  pass `sslrootcert=…` in their JDBC URLs.
- **Restricted database role**: the RDS master (`web3signer_admin`) is
  a member of `rds_superuser` but not a true PostgreSQL superuser. The
  Flyway migration Job creates a scoped `web3signer` login role with
  no SUPERUSER, CREATEDB, CREATEROLE, or REPLICATION privileges; the
  bootstrap tool refuses to touch a role that has any of those set
  already (see PR #105 for the RDS-authority correction).

## Why the shared-signer tier is one Deployment

At the current fleet size (a modest number of signing validators, room
for many more — current observed total is on the [live
portal](https://g.j2d3.com)), one Web3Signer Deployment with vertical
resources is the right shape:

- **JVM startup cost is fixed per Pod, not per key.** Adding another
  validator adds a keystore-decrypt operation at startup but no new
  Pod overhead.
- **RDS connection-pool overhead is centralized.** One pool of JDBC
  connections serves all validators; a per-validator signer would fan
  out that connection count and hit the small-instance limit.
- **Slashing-protection concurrency is naturally serialized.** The DB
  serializes conflicting-attestation checks; a single-signer topology
  cannot race itself.

The tradeoff is availability. One signer means no HA. PRD §15.1
describes the cell-based sharding path (small groups of validators,
each with a redundant signer pair backed by a replicated DB) — but
that shape is not needed until the fleet is large enough to justify
the operational cost.

## JVM heap sizing (the scrypt story)

Web3Signer runs on the JVM. Container-default heap sizing is ~25% of
the container memory limit — 248 MiB for a 1 GiB Pod. Standard EIP-2335
scrypt keystores use `N=262144, r=8, p=1`, which needs ~256 MiB during
decrypt. That combination OOM-killed the signer at first-key load in
PR #117.

Fix: explicit `JAVA_TOOL_OPTIONS: -Xms128m -Xmx640m`. 640 MiB max heap
handles scrypt + steady-state signing objects; 384 MiB native headroom
covers JVM metaspace, direct buffers, thread stacks, and JNI-side
crypto working memory. Web3Signer decrypts keys sequentially at
startup, so peak heap is per-key not per-fleet.

Same pattern appeared one layer up in PR #114 (the onboarding tool's
`hashlib.scrypt` needed explicit `maxmem` — OpenSSL's default was too
tight for standard EIP-2335 profiles).

## VC-side slashing delegation

Both validator client vendors ship a flag to disable their own local
slashing enforcement:

- Lighthouse VC: `--disable-slashing-protection-web3signer` — VC does
  not require Web3Signer to also enforce; the VC still initializes a
  local SQLite for its own bookkeeping (`--init-slashing-protection`).
- Teku VC:
  `--validators-external-signer-slashing-protection-enabled=false` —
  VC does not require the external signer to enforce.

Web3Signer + RDS is the single authoritative layer. This is what makes
stop / reactivate / client-swap safe: the VC's local state is
disposable; the history that keeps you unslashed lives in RDS.

## What can go wrong

| Failure | Detection | Mitigation |
|---|---|---|
| Web3Signer Pod crash | Prometheus target down | Deployment restart; Kubernetes handles it |
| RDS Single-AZ outage | Signer's `permitted` metric flat + JDBC errors | Manual snapshot + restore in another AZ; the lab does not drill this |
| Key material leak via Secrets Manager mis-scope | AWS CloudTrail unusual access | IAM policy enumerates exact ARNs; Terraform `for_each` makes new grants explicit |
| VC signing without checking Web3Signer | Web3Signer's `prevented` counter increases while VC still emits | Neither VC bypasses; the delegation flags mean the VC delegates, not that it lies |
| Ephemeral network config fetch fails | Signer Pod stuck starting | Config is committed as a ConfigMap after PR #115; no runtime fetch needed |
| Shared ExternalSecret eviction across all keys | ExternalSecret condition Not-Ready; existing signer Pod keeps running with cached secret until restart | Non-blocking today; per-validator ExternalSecret split becomes worth the operational cost when the fleet is larger |

## Signing chain, end to end

1. Validator client observes it has a duty (attestation, block
   proposal, sync-committee message).
2. VC constructs the unsigned message and sends it to Web3Signer's HTTP
   API with the public key.
3. Web3Signer looks up the private key by pubkey. Decrypts in-memory
   using the paired password from the projected secret file.
4. Web3Signer queries RDS: is this attestation slot/target-epoch, or
   this proposal slot, already recorded for this pubkey with a
   conflicting message?
5. If yes → refuse. Emit `prevented` metric. Return an error to the
   VC.
6. If no → record the message intent in RDS *before* signing.
7. Sign. Return the signed message to the VC.
8. VC publishes to the beacon.

Each step has a durable audit trail: the RDS record for step 6, the
Prometheus metric for step 5, the CloudTrail log for step 3's
`GetSecretValue`, and the on-chain public record for step 8.

## References

- Onboarding: [`secrets-and-key-projection`](secrets-and-key-projection.md)
- Terraform: [`terraform-aws-foundation`](terraform-aws-foundation.md)
- Boundaries: [architecture/safety-and-custody-boundaries](../architecture/safety-and-custody-boundaries.md)
- Bootstrap tool: `hack/bootstrap-web3signer-database.py`
- Deployment: `platform/apps/base/web3signer/deployment.yaml`
- Related PRs: #103–#108 (bootstrap chain), #110–#117 (Web3Signer live
  bring-up), #127/#128/#133/#135 (multi-validator container extension),
  #132 (Teku VC adapter)
