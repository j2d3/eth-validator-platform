# EKS Spot rebalance and validator fail-closed recovery

## Observation identity

| Field | Value |
|---|---|
| UTC window | 2026-08-05 09:21–09:55 |
| Flux source at the start of the event | `5f51776a497cdafbf09bb607d84b22b09d8eeb7e` |
| Flux source after the observability-only reconciliation | `6ad100bc4775c4531741823fbd7d5a1bcf48c4d1` |
| Environment | Amazon EKS, `dev`, `us-west-2` |
| Network | Ephemery generation 162 |
| Capacity | Zonal EKS managed node groups using Spot instances |

No cloud account or instance identifier, ARN, network address, signing key,
validator public key, secret value, or Kubernetes credential is included in
this record.

## Event

EC2 issued rebalance recommendations for one Spot instance in each of two
Availability Zones. In both zones, the Auto Scaling group launched a
replacement before terminating the at-risk instance. The replacement launches
completed within seconds. One termination completed shortly afterward; the
other completed after the managed-node-group drain window.

The event rescheduled:

- the Geth + Lighthouse node pair;
- the Nethermind + Prysm node pair; and
- validator 04's Teku validator client.

The shared Web3Signer and RDS PostgreSQL slashing database were outside the
replaced Spot workers and did not restart.

## Chain-data recovery

Both node-pair StatefulSets reused their existing encrypted EBS claims. During
the Geth + Lighthouse move, Kubernetes briefly reported that the two
single-writer volumes were still attached to the previous worker. The
execution claim attached to the replacement 16 seconds later and the consensus
claim 22 seconds later. The warning cleared without deleting or replacing a
claim. Both Nethermind + Prysm claims attached 15 seconds after that pair's
initial warning.

After recovery:

| Pair | EL head changes over 15 minutes | CL head changes over 15 minutes | EL / CL peers | CL slot lag |
|---|---:|---:|---:|---:|
| Geth + Lighthouse | 17 | 16 | 6 / 4 | 0 |
| Nethermind + Prysm | 16 | 14 | 20 / 9 | 0 |

All nine execution targets and all nine consensus targets were up. Both moved
pairs were advancing from their retained databases rather than starting with
empty chain data.

## Validator-client recovery

Validator 04's Teku client was evicted from the terminating worker and created
on a replacement worker. Its single-writer validator-data claim initially
remained attached to the terminating worker; the claim attached to the
replacement after 89 seconds. The container then started, and the new Pod
became Ready 119 seconds after scheduling with zero container restarts. It
then:

1. reached the private Web3Signer Service;
2. loaded exactly one remote validator identity; and
3. re-entered doppelganger detection before attempting duties.

At 09:53:13 UTC, after observing epochs 1260 through 1262, Teku reported that
the doppelganger check had finished and that no duplicate validator was
detected. At 09:54:00 UTC, the same validator client reported publishing one
attestation. The record therefore distinguishes Pod readiness, safety-gate
clearance, and the first attributable post-restart duty as separate events.

During this gate, the fleet still reported four enabled validator targets,
Web3Signer reported four loaded keys, and its counters reported zero prevented
slashing checks and zero missing signing identifiers. Pod readiness therefore
did not imply immediate resumption of signing.

## What this establishes

- EKS managed-node-group capacity rebalancing launched replacement Spot
  capacity in the required zones.
- Kubernetes rescheduled stateful clients and reattached retained encrypted EBS
  claims after the old workers released them. This included both chain data and
  validator-client local data.
- Geth + Lighthouse and Nethermind + Prysm resumed head progression without
  replacing their chain-data identities.
- A remote-signing validator client restarted without copying its signing key
  onto the worker, failed closed during doppelganger detection, and cleared the
  check before publishing a subsequent attestation.
- Web3Signer and RDS slashing history remained independent of worker
  replacement.

## What this does not establish

- zero missed duties during the replacement and doppelganger window;
- uninterrupted validator availability from a single Spot duty path;
- graceful client shutdown within every two-minute Spot interruption notice;
- recovery when replacement capacity is unavailable in the EBS volume's zone;
- recovery from EBS, RDS, Web3Signer, or control-plane failure;
- production or funded-mainnet suitability; or
- the still-unperformed warm-pause and cold-rebuild procedures.

This observation supports keeping Spot for bounded testnet exercises. It also
shows why a production duty path needs explicit availability and interruption
objectives rather than treating successful rescheduling as uninterrupted
service.
