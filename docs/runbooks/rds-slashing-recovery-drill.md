# RDS slashing-history recovery drill

## Scope and evidence boundary

This runbook is a **design and procedure**, not a record. As of this commit the
drill has never been run. No AWS resource has been created or modified for it,
no snapshot or restore has been taken, no PostgreSQL connection has been opened,
and no secret value has been read. Live signing is unchanged.

The drill answers one question that the [first signing
evidence](../evidence/2026-08-04-first-signing-validator.md) explicitly does not:
**if the slashing database had to be recovered, would the recovered copy be a
usable signing authority?** A restore that completes is not an answer. The
answer requires schema compatibility, record continuity, and a demonstrated
refusal of a conflicting duty.

The machine-readable form of everything below is
[`hack/qualification/rds-slashing-recovery-drill.yaml`](../../hack/qualification/rds-slashing-recovery-drill.yaml).
That contract is checked against the Terraform declarations in
`terraform/environments/dev` by a non-mutating preflight:

```bash
make rds-drill-readiness
```

The preflight reads files. It calls no AWS API, opens no database connection,
and reads no secret. Its output is a readiness report and is deliberately not
recovery evidence.

## Preconditions

1. `make check` passes on a clean checkout of the revision being drilled.
2. `make rds-drill-readiness` passes, so every recovery guard the drill depends
   on is still declared in Terraform.
3. The reviewed change that adds the drill security group and the drill signer
   manifests has merged. Those declarations are **not** in this repository yet;
   introducing them is a separate pull request, because merging them creates
   AWS resources.
4. A drill-only BLS key exists: generated offline, never deposited, holding no
   funds, absent from `applications/validators/` and from every Secrets Manager
   signing container.
5. Two named humans are available — one operator, one approver. They must not be
   the same person for the `human-go-no-go` gate.

## Gate sequence

Each gate must pass before the next begins. The order is the safety argument,
not a convenience: signing stops before anything is recovered, and a human
decides before anything is billed.

| # | Gate | Mutates AWS | Bills | Human approval |
|---|---|---|---|---|
| 1 | `signing-disabled` | no | no | no |
| 2 | `source-fingerprint` | no | no | no |
| 3 | `human-go-no-go` | no | no | **yes** |
| 4 | `restore-isolated-target` | yes | yes | no |
| 5 | `schema-compatibility` | no | no | no |
| 6 | `row-continuity` | no | no | no |
| 7 | `conflicting-duty-rejection` | yes | yes | no |
| 8 | `cleanup` | yes | no | no |
| 9 | `evidence` | no | no | no |

### 1. `signing-disabled`

Signing stops first, in Git, and is then observed to have stopped. A drill that
begins while a validator can still request a signature is not a drill.

1. Merge one reviewed change setting `suspend: true` in
   `clusters/dev/node-apps.yaml`. This removes the validator clients, which are
   the only thing that asks Web3Signer for a signature.
2. Wait for the validator-client Pods to be gone. Do not proceed on a
   `Terminating` Pod.
3. Record the signer's `permitted` and `prevented` counters and the current
   attestation and block row counts. These are the last live values; gate 2
   fingerprints against them.
4. Merge a second reviewed change setting `suspend: true` in
   `clusters/dev/apps.yaml`. This removes Web3Signer itself.
5. Confirm zero Web3Signer Pods and zero validator-client Pods.

Leave `signer-prerequisites`, `signer-infrastructure-configs`, and
`infrastructure-*` reconciling. Suspending them would tear down the credential
and TLS paths the later gates need, without making anything safer.

**Abort if** any signer or validator-client Pod is still running, or if the
signer's counters are still advancing.

### 2. `source-fingerprint`

Record what recovery must reproduce, without recording anything sensitive.

1. Read the source instance's latest restorable time and its automated-backup
   status through read-only AWS describe calls. Choose the recovery point: the
   latest restorable time at or after the moment gate 1 completed.
2. Confirm the chosen recovery point is inside the automated-backup retention
   window declared in Terraform.
3. Run the aggregate-only fingerprint queries from the contract's
   `verification.continuity.queries` against the **source** database over a
   `verify-full` TLS connection, as a read-only session.

The fingerprint is counts, minima, maxima, and one salted digest. The salt is
generated per drill, lives only in the operator session, is never committed and
never logged, and is discarded at cleanup. It exists so that the digest can
prove continuity without a public key entering the comparison output.

Nothing in the fingerprint output may name a validator. The preflight rejects
any contract query whose top-level projection is not an aggregate or digest call
with an explicit alias, which is what mechanically keeps public keys and signing
roots out of the record.

**Abort if** the recovery point falls outside the retention window, or if any
query returns a non-aggregate column.

### 3. `human-go-no-go`

Everything up to here is free and reversible. Everything after gate 3 costs
money and creates AWS resources. This gate is where a human accepts that.

The operator presents, and the approver reviews:

- the exact restore target identifier, derived as
  `{source_identifier}-drill-{utc_date}`, confirmed not to collide with the
  source identifier or any existing instance;
- the exact recovery point chosen at gate 2;
- the estimated cost and the maximum drill lifetime;
- the drill security group, confirmed to grant no ingress from the live signer
  or migration security groups;
- the cleanup plan and who executes it.

The approver records an explicit **go** or **no-go**. A no-go ends the drill
here; run gate 8 anyway (there is nothing to delete, but the salt is discarded)
and publish the readiness report as a no-go record.

There is no implicit approval, no default-yes, and no approval carried over from
a previous drill.

### 4. `restore-isolated-target`

Point-in-time restore to a **new** instance. The source instance is not stopped,
not modified, not rebooted, and not failed over.

Required properties of the restore target:

- an identifier distinct from the source;
- the existing isolated database subnet group, which has no internet-gateway and
  no NAT route;
- the drill security group only — never the live database security group;
- `publicly_accessible = false`;
- Single-AZ, matching the source class and storage;
- deletion protection **off**, so gate 8 can actually delete it.

The restore inherits the source's encryption and its parameter group's TLS
requirement. Verify both on the restored instance before using it.

**Abort if** the restore fails, if the identifier collides, or if the restored
instance comes up attached to any security group that the live signer can reach.

### 5. `schema-compatibility`

Prove the restored copy is a database the pinned Web3Signer image would accept,
without migrating it.

Run the contract's `verification.schema.queries` as a read-only session against
the restored copy and require:

- the applied migration version equals the version the pinned Web3Signer image
  ships and the Flyway Job applied;
- every migration in the history is recorded successful;
- the table inventory matches the expected set.

Take the expected table set from the migration files inside the pinned
Web3Signer image at drill time. The contract lists them for review, but the
image is the authority, and the list must be re-derived if the image is bumped.

Do **not** run Flyway against the restored copy. A migration that repairs the
restored copy would destroy the thing being measured.

**Abort if** the applied version differs, any migration is recorded failed, or a
table is missing.

### 6. `row-continuity`

Run the same aggregate-only queries used at gate 2, now against the restored
copy, with the same salt. Compare:

- validator row count equals the source count;
- attestation row count is greater than or equal to the source count;
- block row count is greater than or equal to the source count;
- every low-watermark minimum is not lower than the source minimum;
- the fingerprint digest equals the source digest for the rows covered by the
  recovery point.

The inequalities are deliberate. A restore taken at a recovery point after the
fingerprint may legitimately contain more rows; it may never contain fewer, and
a watermark may never move backwards. A lowered watermark is the specific shape
of corruption that would let a recovered signer re-sign history.

**Abort if** any comparison fails. A restored copy that lost a row is not a
signing authority, and no amount of subsequent testing makes it one.

### 7. `conflicting-duty-rejection`

Continuity proves the data survived. This gate proves the recovered database
still *enforces*.

The test uses the drill-only key and never a fleet key. Signing with a fleet key
against a restored copy would write a slashing record that the live database
never sees — which is exactly the divergence the drill exists to rule out. So
continuity is proven by comparison (gate 6) and enforcement is proven separately
on a key with no live history.

1. Start the drill Web3Signer instance. It is bound to the restored copy only.
   It has no beacon connection, no validator client, and no publication path.
2. Load only the drill-only key.
3. Request one attestation signature for a chosen source and target epoch
   through the signer's HTTP API. Expect success.
4. Request a second attestation signature for the same key and the same target
   epoch with a different signing root.

Expected: HTTP `412`, the `prevented` counter increments by one, and the
`permitted` counter is unchanged from step 3.

**Abort if** the second request returns a signature. That means the restored
copy is not enforcing, and the drill has found a real defect: keep signing
disabled and open an issue before anything else.

### 8. `cleanup`

Cleanup runs whether the drill passed, failed, or was aborted.

1. Delete the drill signer and its namespace.
2. Delete the drill-only key material.
3. Delete the restored instance with no final snapshot and no retained automated
   backups. It is a copy; retaining it doubles the number of places slashing
   history lives, which is a liability, not a backup.
4. Delete the drill security group.
5. Discard the fingerprint salt.
6. Confirm by read-only describe calls that no drill-named resource remains.
7. Record observed cost against the estimate.

The drill target's maximum lifetime is six hours. Exceeding it is an abort
condition in its own right, because an unattended restored copy of slashing
history is a standing exposure.

### 9. `evidence`

Publish a record under [`docs/evidence/`](../evidence/) that:

- names the tested repository commit and the UTC window;
- states each gate's pass or fail as an assertion, separately from operator
  interpretation;
- states what the drill does **not** establish — at minimum: Multi-AZ failover,
  signer high availability, concurrent-signer semantics, behaviour under load,
  and anything about mainnet;
- contains no account identifier, ARN, instance identifier, endpoint hostname,
  IP address, connection string, public key, or secret value.

Then flip `status.executed` and set `status.evidence_record` in the drill
contract in the same pull request as the evidence file.

Re-enabling signing is a **separate** reviewed change made after the evidence is
published. It is never part of the drill session.

## Cost

Indicative list prices for the declared lab shape — one `db.t4g.micro` Single-AZ
instance with 20 GiB `gp3` storage, restored for at most six hours in the same
region and VPC. This is a bound to review at gate 3, not a quote; re-check
current regional prices then.

| Item | Basis | Bound |
|---|---|---|
| Restored instance hours | one small instance, ≤ 6 h | well under USD 1 |
| Restored storage | 20 GiB `gp3`, prorated | cents |
| Incremental backup storage | only if the copy is retained, which cleanup forbids | none |
| Data transfer | in-VPC, same region | none |

The source instance is neither stopped nor modified, so the drill adds nothing
to its cost. The cost controls are the six-hour lifetime bound and the mandatory
cleanup gate, not the price of any single item.

## Failure handling

Any abort condition stops the drill immediately. On abort:

- leave signing disabled;
- run gate 8 anyway;
- publish a redacted failure record under `docs/evidence/`;
- open a follow-up issue before signing is re-enabled.

A failed drill is a successful outcome for the platform: it found the defect
with signing off and no funds at risk, which is the entire point of running it
before the recovery is needed.

## What a passing drill would establish

That, for this environment and this recovery point, a point-in-time restore
produces a database with the expected Web3Signer schema, no lost slashing
records, no reversed watermark, and working conflicting-duty enforcement.

## What a passing drill would not establish

- Multi-AZ failover or any availability property of the source instance.
- Web3Signer high availability or concurrent-signer semantics.
- Behaviour at fleet scale, under load, or over long durations.
- Slashing-history export/import between database engines.
- Measured RPO or RTO. The drill measures correctness, not duration; timing
  numbers collected during it are observations, not objectives.
- Anything about mainnet.

## References

- Contract: [`hack/qualification/rds-slashing-recovery-drill.yaml`](../../hack/qualification/rds-slashing-recovery-drill.yaml)
- Preflight: `tools/verify_rds_recovery_drill_preflight.py`
- Terraform: [`terraform/environments/dev/signer-foundation.tf`](../../terraform/environments/dev/signer-foundation.tf)
- Signing tier: [`components/web3signer-and-slashing-protection.md`](../components/web3signer-and-slashing-protection.md)
- Layer suspend contract: [`eks-flux-bootstrap.md`](eks-flux-bootstrap.md)
- Recovery priorities and objectives: PRD §14.2 and §14.3
- Production gap: [`production-evolution.md`](../production-evolution.md)
