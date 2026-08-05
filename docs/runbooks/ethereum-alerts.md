# Ethereum and signer alert response

This runbook covers the Prometheus alerts defined by the Ethereum node chart
and the shared Web3Signer application. The current lab exposes alerts in
Grafana and Alertmanager. It does not configure an external email, paging, or
chat receiver, so the alert list is an observation surface rather than a paging
guarantee.

Open the [Grafana alert list](https://ops.g.j2d3.com/grafana/alerting/list) and
use the alert labels to select the affected assignment, component, client pair,
cluster, and environment. Keep validator public keys, keystores, passwords,
mnemonics, withdrawal credentials, AWS identifiers, and raw environment dumps
out of incident notes.

## Pair target unavailable

`EthereumPairTargetUnavailable` means the normalized execution or consensus
metrics target was absent or down for five minutes.
`EthereumValidatorTargetUnavailable` is rendered only for a signing-enabled
assignment and means its validator-client target was absent or down for two
minutes.

1. Check the assignment's lifecycle state and recent Flux reconciliation.
2. Check Pod scheduling, readiness, volume-attachment, and node events.
3. Open the pair dashboard and verify whether the other client target, head,
   peers, and resource metrics remain available.
4. During a Spot interruption, distinguish a bounded reschedule from a failed
   replacement. Do not label Pod readiness as chain recovery; require head
   progression after the move.

A reviewed stop removes the active chart resources and their pair alert rules.
Do not silence an active assignment merely because interruption is expected.

## Head stall and consensus lag

- `EthereumExecutionHeadStalled` and `EthereumConsensusHeadStalled` require a
  rolling 15-minute head-change count below one for another five minutes.
- `EthereumConsensusSlotLagHigh` requires more than eight slots of lag for five
  minutes.
- `EthereumConsensusFinalityLagHigh` requires more than four epochs of lag for
  ten minutes.

Confirm the network generation, peer counts, execution/consensus Engine API
relationship, and current head progression. A network-wide testnet halt can
produce the same signals as a local failure; compare multiple client pairs
before replacing data. On Ephemery, an old consensus database can refuse
checkpoint sync after a generation change. Replace chain data only through the
generation-scoped lifecycle procedure; do not delete validator or signer data.

## Signer unavailable or key-count mismatch

`Web3SignerUnavailable` and `Web3SignerKeyCountBelowEnabledValidators` are
critical because an enabled validator may be unable to perform a duty.

1. Verify Web3Signer, ExternalSecret, and RDS connectivity without printing
   secret values.
2. Compare enabled validator count with loaded signer-key count. More loaded
   keys than enabled validators is allowed during staged onboarding; fewer is
   not.
3. Inspect the affected validator client's remote-signer and doppelganger state.
4. Keep the validator stopped if signer identity, network binding, or slashing
   history is ambiguous.

## Prevented signing and unknown-key requests

`Web3SignerSlashingCheckPrevented` is a critical safety event. Do not retry the
request manually and do not bypass, truncate, recreate, or delete the RDS
slashing-protection database. Preserve the request time, duty type, assignment,
client logs, signer logs, and database backup/health evidence, with key material
redacted. Determine whether the event was a valid duplicate rejection, a stale
validator process, or conflicting desired state before resuming duties.

`Web3SignerUnknownKeyRequest` is a warning that the validator client requested
an identity absent from Web3Signer. Compare the Git catalog, projected
ExternalSecret descriptors, signer key count, and validator-client assignment.
Do not rotate or replace a validator key merely to clear the alert.

## Persistent volume capacity

`EthereumPersistentVolumeUtilizationHigh` means a mounted execution,
consensus, or validator claim remained above 85% filesystem utilization for
30 minutes. `EthereumPersistentVolumeProjectedFull` means the same claim is
projected to fill within seven days for 30 minutes. The projection uses the
positive slope of six hours of used-byte samples, is withheld until the rule
existed at least five hours ago, and ignores growth at or below 1 KiB/s. A
missing projection therefore means insufficient history or no material
positive growth; it does not mean the claim has unlimited capacity.

1. Select the `assignment_id` and `persistentvolumeclaim` from the alert, then
   open the validator-detail dashboard's storage panels. The claim suffix
   identifies the execution, consensus, or validator data role.
2. Compare utilization, recent growth, client sync phase, and the configured
   claim size. Initial sync can grow faster than steady state; do not treat one
   extrapolation as a stable long-term forecast.
3. Verify the StorageClass permits expansion and that the EBS volume and
   filesystem can be expanded through the reviewed storage procedure. EBS
   `gp3` claims can expand but cannot shrink.
4. Do not delete, replace, or recreate a claim merely to clear the alert.
   Validator-client data is not the RDS slashing-protection record, but
   deleting it can still disrupt doppelganger state and duties. Execution and
   consensus data replacement must remain generation- and client-aware.
5. After expansion or other remediation, require both alert resolution and
   continuing client head/duty evidence. A cleared capacity expression alone
   does not establish validator health.

The recording rules scope kubelet volume statistics by joining
`(namespace, persistentvolumeclaim)` to allowlisted platform labels on
`kube_persistentvolumeclaim_labels`. They do not infer assignment identity
from generated PVC names and do not emit validator public keys.

## Resolution evidence

Record when the alert fired and resolved, the desired-state commit, the
relevant Grafana interval, and whether an attributable post-recovery head change
or validator duty occurred. A resolved alert proves the expression cleared; it
does not by itself prove zero missed duties or production availability.
