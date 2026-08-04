# First Web3Signer-backed validator duty on EKS

## Observation

| Field | Value |
|---|---|
| UTC time | 2026-08-04 12:15:24 |
| Repository revision reconciled by Flux | `e102fe24b2cdd5d1606c01ac3997c93de954c172` |
| Implementation changes | PRs #118, #119, and #120 |
| Environment | Amazon EKS, `dev`, `us-west-2` |
| Network | Ephemery generation 162 |
| Client pair | Geth + Lighthouse |
| Validator index | `30201` |
| Activation | epoch `1060`, state `active_ongoing` |
| First observed duty | unaggregated attestation, slot `33927`, committee `5` |

No private key, keystore, password, secret value, account identifier, ARN,
network address, or Kubernetes credential is included in this record.

## Preconditions observed

- All eight Flux Kustomizations were Ready on the same `main` revision.
- The Geth and Lighthouse pair was Ready, had non-zero peers, and reported no
  execution sync distance or consensus slot lag.
- Lighthouse connected to one available, synced beacon endpoint.
- Web3Signer loaded exactly one BLS signing identity.
- The validator client mounted no signing key or keystore password. It used the
  private Web3Signer HTTP endpoint.
- The validator initialized its local Lighthouse slashing database with mode
  `0600`. Web3Signer remained the authoritative slashing-protection service for
  the remote key.
- The validator's custom-network bootstrap data matched the catalog's Ephemery
  genesis time and validators root.
- Lighthouse completed doppelganger detection and logged that no duplicate
  validator was found before starting duties.

## Duty evidence

At slot `33927`, Lighthouse logged one successfully published unaggregated
attestation for validator index `30201`. The validator's Prometheus metrics
then showed one completed attestation publication task.

Web3Signer metrics showed:

| Metric | Observed value |
|---|---:|
| BLS signing events | 2 |
| Slashing checks permitted | 2 |
| Slashing checks prevented | 0 |
| Attestation operations against the slashing database | 1 |
| Loaded signers | 1 |
| Missing signing identifiers | 0 |

The beacon API returned:

- validator state `active_ongoing`;
- balance and effective balance of 32 ETH;
- `slashed=false`; and
- `is_live=true` for epoch `1060`.

The validator and Web3Signer Pods were Ready with zero restarts after the duty.

## What this establishes

This observation covers one complete testnet duty path:

1. Flux reconciled the reviewed signing assignment.
2. Lighthouse obtained a duty from the synced beacon node.
3. Lighthouse requested a remote signature from Web3Signer.
4. Web3Signer checked and wrote slashing state in RDS PostgreSQL.
5. Web3Signer returned the signature.
6. Lighthouse published the attestation.
7. The beacon API reported the validator live.

## What this does not establish

- long-term attestation effectiveness or reward performance;
- block proposal or sync-committee performance;
- safe stop, reactivation, archive, or client migration;
- slashing-history export/import, point-in-time recovery, or rejection of a
  deliberately conflicting duty;
- RDS failover or Multi-AZ availability;
- Web3Signer high availability;
- production suitability, mainnet readiness, or behavior at fleet scale.

The validator remained running after this evidence was collected.
