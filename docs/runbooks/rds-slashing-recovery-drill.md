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
3. The reviewed change that adds the drill security group, the drill-only
   connection `ExternalSecret`, and the drill signer manifests has merged. Those
   declarations are **not** in this repository yet; introducing them is a
   separate pull request, because merging them creates AWS resources.
4. A drill-only BLS key exists: generated offline, never deposited, holding no
   funds, absent from `applications/validators/` and from every Secrets Manager
   signing container.
5. Two named humans are available — one operator, one approver. They must not be
   the same person for the `human-go-no-go` gate.

## Gate sequence

Each gate must pass before the next begins. The order is the safety argument,
not a convenience: signing stops before anything is recovered, and a human
decides before anything is billed.

| # | Gate | Mutates AWS | Changes cluster state | Bills | Human approval |
|---|---|---|---|---|---|
| 1 | `signing-disabled` | no | **yes** | no | no |
| 2 | `source-fingerprint` | no | no | no | no |
| 3 | `human-go-no-go` | no | no | no | **yes** |
| 4 | `restore-isolated-target` | yes | no | yes | no |
| 5 | `schema-compatibility` | no | no | no | no |
| 6 | `row-continuity` | no | no | no | no |
| 7 | `conflicting-duty-rejection` | yes | yes | yes | no |
| 8 | `cleanup` | yes | yes | no | no |
| 9 | `evidence` | no | no | no | no |

The two mutation columns are separate because they are different risks. Gate 1
merges two reviewed Git changes and removes running workloads — a real change,
and the contract records it as one — but it creates, modifies, and bills no AWS
resource. The ordering rule the drill enforces is about AWS: nothing is created
or billed before gate 3.

### 1. `signing-disabled`

Signing stops first, in Git, and is then observed to have stopped. A drill that
begins while a validator can still request a signature is not a drill.

This gate changes desired state and removes running workloads. It creates,
modifies, and bills no AWS resource, which is why it is allowed to run before
the human go/no-go.

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
3. Run the aggregate-only queries from the contract's
   `verification.schema.queries` and `verification.continuity.queries` against
   the **source** database over a `verify-full` TLS connection, as a read-only
   session. Record every returned value; gate 5 and gate 6 compare against them.

The fingerprint is one per-table digest for each safety-bearing table, plus the
schema-object digests. Each row is reduced to `md5(:drill_salt || row::text)`,
so every column takes part — public key, signing root, epochs, slots, and any
column a future migration adds — while no raw value is ever projected. The
per-row digests are then aggregated with `md5(string_agg(..., ORDER BY ...
COLLATE "C"))`, which is order-independent and therefore comparable across two
databases.

Everything is PostgreSQL core. The pinned Web3Signer migrations do not create
`pgcrypto`, so `hmac()` and `digest()` do not exist on this database, and
installing an extension would be a mutation this drill is not permitted to make.
The preflight rejects any contract query that calls one.

The salt is generated per drill, lives only in the operator session, is never
committed and never logged, and is discarded at cleanup. It exists so that a
digest recorded during the drill is not a lookup table for a public key.

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

A point-in-time restore does **not** reproduce the source's placement,
networking, or parameters. Unless each value is supplied on the restore call,
AWS creates the target in a system-selected availability zone with the *default*
VPC security group, the *default* DB subnet group, and the *default* DB
parameter group — and the default parameter group does not set
`rds.force_ssl = 1`, so it accepts plaintext connections. What the restore does
carry over is storage encryption and its KMS key, the engine and version, and
the database contents at the recovery point, including every PostgreSQL role and
that role's password.

Supply all of these explicitly, and confirm each afterwards with a read-only
`describe-db-instances` on the restored copy before running any query:

| Restore parameter | Required setting | What AWS would do if it were omitted |
|---|---|---|
| `--db-subnet-group-name` | the existing isolated database subnet group | the default DB subnet group |
| `--vpc-security-group-ids` | the drill security group and nothing else | the VPC's default security group |
| `--db-parameter-group-name` | the custom group that sets `rds.force_ssl = 1` | the engine default group, which does not force TLS |
| `--no-publicly-accessible` | public accessibility off | derived from the subnet group, not guaranteed |
| `--no-multi-az` | Single-AZ | the source's Multi-AZ setting |
| `--availability-zone` | one AZ of the isolated database subnet group | a system-selected zone |
| `--deletion-protection false` | off, so gate 8 can delete the copy | the source's setting, which is on |
| `--backup-retention-period 0` | no recovery points of the copy's own | the source's seven days |

Then confirm the identifier is distinct from the source, that TLS is actually
enforced by attempting a non-TLS connection and being refused, and that the
reported backup retention is zero. If retention is not zero, set it to zero with
an explicit modify and re-verify before proceeding.

**Connecting to the copy.** The live `web3signer-database` Secrets Manager object
and the live `ExternalSecret` and Secret it feeds are not modified, not
repointed, and not deleted. Roles and passwords are part of the restored data, so
the restored copy already accepts the application role the live signer uses. A
**drill-only** `ExternalSecret` in the drill namespace reads the same Secrets
Manager object into a Secret with a **distinct target name**, and the operator
supplies the restored endpoint as the host at drill time — it is never committed
and never written back to Secrets Manager. The restored copy's RDS-managed master
credential is never read; nothing in the drill handles a password value. The
drill Secret is deleted at gate 8.

**Abort if** the restore fails, if the identifier collides, if any explicit
parameter above is missing from the restore call or disagrees with the describe
output, if a non-TLS connection succeeds, or if the restored instance comes up
attached to any security group that the live signer can reach.

### 5. `schema-compatibility`

Prove the restored copy is a database the pinned Web3Signer image would accept,
without migrating it.

Run the contract's `verification.schema.queries` as a read-only session against
the restored copy and require all of:

- **Flyway history**: exactly twelve applied migrations, a highest installed
  rank of twelve, every one recorded successful, and a digest of the version set
  exactly equal to the source's. Flyway's `version` column is a string, so the
  count, the rank, and the digest are used rather than a lexical maximum.
- **Web3Signer's own schema version**: exactly one row in `database_version`
  with `version = 12`. This is the value the signer itself reads to decide
  whether the database is one it will accept, and a complete Flyway history does
  not imply it.
- **The exact table inventory**: `table-inventory-digest` returns
  `md5` of the base-table names of schema `public`, sorted under the `C`
  collation and joined with commas, and it must equal the digest declared in the
  contract. A count would accept seven arbitrary tables; this does not. The
  preflight recomputes the declared digest from the declared table list, so the
  two cannot drift apart.
- **The slashing-critical schema objects**: the constraint, index, and
  routine/trigger digests must each be exactly equal to the value the same query
  returns against the source. That covers the uniqueness that actually enforces
  safety — one row per public key in `validators`, at most one block per
  validator per slot, at most one attestation per validator per target epoch, one
  watermark row per validator — and it also catches an object the source does not
  have, such as a trigger added to `signed_blocks`.

The contract lists the expected tables and the expected uniqueness for review,
but the pinned image's migration files are the authority. Re-derive both at drill
time, and re-derive them again if the image is bumped; a difference is an abort,
not an edit to the contract.

Do **not** run Flyway against the restored copy. A migration that repairs the
restored copy would destroy the thing being measured.

**Abort if** the Flyway history is not twelve successful migrations, if
`database_version` is not a single row of 12, if the table inventory digest
differs from the declared digest, or if any schema-object digest differs from the
source.

### 6. `row-continuity`

Run the same continuity queries used at gate 2, now against the restored copy,
in the same session with the same salt. Every safety-bearing table is covered:
`validators`, `signed_blocks`, `signed_attestations`, `low_watermarks`, and
`metadata`.

**The comparison is exact equality**, per table, of both the row count and the
aggregate digest — and of every low-watermark minimum and maximum. Not "at
least as many rows".

Signing stopped at gate 1, the source fingerprint was taken after that, and the
recovery point is at or after the same moment. No row can therefore legitimately
differ, so any difference at all is a failure. Tolerating "more rows than the
source" would also mean tolerating a *replaced* interior row, which counts,
minima, and maxima cannot see: a signed attestation whose signing root changed
leaves the count unmoved. The per-row digest sees it.

A lowered watermark is the specific shape of corruption that would let a
recovered signer re-sign history, so the watermark extents are compared as well
as digested — a digest mismatch says something changed, and the extents say
whether a watermark moved and in which direction.

**Abort if** any count, digest, or extent differs. A restored copy that lost,
gained, or altered a row is not a signing authority, and no amount of subsequent
testing makes it one.

### 7. `conflicting-duty-rejection`

Continuity proves the data survived. This gate proves the recovered database
still *enforces*.

The test uses the drill-only key and never a fleet key. Signing with a fleet key
against a restored copy would write a slashing record that the live database
never sees — which is exactly the divergence the drill exists to rule out. So
continuity is proven by comparison (gate 6) and enforcement is proven separately
on a key with no live history.

1. Start the drill Web3Signer instance. It is bound to the restored copy only,
   through the drill-only connection Secret and never the live one. It has no
   beacon connection, no validator client, and no publication path.
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
2. Delete the drill-only connection `ExternalSecret` and the Secret it created.
   The live credential path is untouched throughout, so there is nothing to
   restore.
3. Delete the drill-only key material.
4. Delete the restored instance with `--skip-final-snapshot` and
   `--delete-automated-backups`. It is a copy; retaining it doubles the number
   of places slashing history lives, which is a liability, not a backup.
5. Delete the drill security group.
6. Discard the fingerprint salt.
7. Confirm by read-only describe calls that no drill-named instance, snapshot,
   automated backup, or security group remains.
8. Record observed cost against the estimate.

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
| Backup storage for the copy | retention supplied as 0 on the restore call | none |
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
produces a database whose schema objects and Web3Signer schema version are the
ones the pinned image expects, whose slashing records match the frozen source row
for row across every safety-bearing table, and which still refuses a conflicting
duty.

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
