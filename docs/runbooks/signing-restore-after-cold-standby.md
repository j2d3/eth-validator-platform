# Signing restore after a cold-standby restore

## Scope and evidence boundary

This runbook is a **design and procedure**, not a record. As of this commit no
signing restore has been performed through it. Nothing here enables signing;
executing the enablement step requires the human gate below plus an ordinary
reviewed Git change. The machine-readable form of this procedure is
[`hack/qualification/signing-restore-qualification.yaml`](../../hack/qualification/signing-restore-qualification.yaml).

The cold-standby runbook proves the platform's compute and data foundation can
be torn down and restored. This runbook answers the question that restore
deliberately leaves open: **when is the restored platform allowed to sign
again, and how little human interaction can that safely require?**

The answer this design commits to: **exactly one human go/no-go gate**, placed
after automated read-only qualification and before an ordinary Git enablement
merge. Interaction is minimized by automating verification, never by removing
authorization. PRD §5.7 (signing is the final readiness gate), §5.9 (fail
closed), and §5.12 (Git merge is ordinary deployment authorization) are the
frame; this procedure adds no new authority.

## Why a fingerprint must be captured at teardown

A restore that completes is not evidence that slashing history survived intact
(the [RDS drill design](rds-slashing-recovery-drill.md) makes the same point
for point-in-time recovery). Continuity can only be judged against a reference
taken **before** the teardown, because after the instance is deleted there is
nothing left to compare with. The guarded `down` therefore captures, before
deleting the RDS instance, an aggregate fingerprint:

- slashing-protection schema version;
- validator identity count;
- per-validator digests of the maximum signed block slot and the maximum
  signed attestation epoch;
- the final snapshot identifier and a UTC capture timestamp.

The fingerprint contains no secret values and is stored beside the recovery
manifest in S3, so it survives cold storage and travels with the state the
restore will consume. A cold state without a fingerprint fails qualification —
restores from snapshots that predate this design require the manual RDS drill
instead.

## Gate sequence

| # | Gate | Mutates AWS | Changes cluster state | Human interaction |
|---|---|---|---|---|
| 1 | `fingerprint-present` | no | no | none |
| 2 | `restored-endpoint-reachable` | no | no | none |
| 3 | `schema-compatibility` | no | no | none |
| 4 | `row-continuity` | no | no | none |
| 5 | `single-slashing-authority` | no | no | none |
| 6 | `signer-prerequisites-ready` | no | no | none |
| 7 | `assignments-still-stopped` | no | no | none |
| 8 | `human-go-no-go` | no | no | **the only gate** |
| 9 | `enablement-merge` | no | yes (via Flux) | ordinary paired review |

Gates 1–7 run unattended after `up` completes and produce a qualification
report. Any failure stops the sequence with nothing to clean up, because
nothing was mutated. Gate 8 requires a named operator and a named approver who
are not the same person, and an issue comment recording the decision with the
report digest. Gate 9 is a normal pull request flipping the approved
assignments from `lifecycle: stopped` to active — the merge is the
authorization (PRD §5.12), and Flux performs the reconciliation.

## What each automated gate defends

- **Row continuity (gate 4)** defends PRD §5.8: recovered history may extend
  but never regress. A digest lower than the fingerprint means signed duties
  are missing from the restored database, and signing on top of it risks
  repeating them.
- **Single slashing authority (gate 5)** defends PRD §5.2: if a drill copy,
  stale replica, or second endpoint is reachable by the signer tier, two
  databases could each believe they are the authority — qualification refuses
  rather than choosing.
- **Assignments still stopped (gate 7)** defends PRD §5.7: qualification
  passing must not itself move anything toward signing. It verifies both the
  Git desired state and the absence of running validator-client workloads.

## Failure disposition

Every failure mode leaves the platform in the state the restore created:
running, healthy, and not signing. There is no partial-enablement state.
Re-running qualification is free because it is read-only. If continuity cannot
be established (fingerprint missing, digests regressed), the path forward is
the full manual RDS drill, not a weaker automated check.

## Relationship to the RDS slashing-recovery drill

The drill (issue #180) qualifies point-in-time recovery of the slashing
database against a *live* source, including a demonstrated conflicting-duty
rejection with a drill-only key. This procedure qualifies the *snapshot*
restore path that the cold-standby lifecycle already exercises, using the
teardown-time fingerprint as its reference. The two share the schema and
continuity checks; the drill remains the stronger, occasional exercise, and
this procedure is the routine one.
