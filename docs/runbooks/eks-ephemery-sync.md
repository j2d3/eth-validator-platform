# Qualify one Ephemery node pair on EKS

## Scope and evidence boundary

This runbook records the node-only qualification that preceded the first
signing activation. Its chain-identity, P2P, sync, and recovery checks still
apply. The current catalog now contains one deposited signing identity, and the
non-signing lifecycle workflow deliberately refuses that assignment.

Current state on 2026-08-04:

- Geth + Lighthouse is active with one signing validator;
- Reth + Lighthouse is active without validator duties;
- both pairs have non-zero peers and advancing heads; and
- the first Web3Signer-backed attestation is recorded in
  [the signing evidence](../evidence/2026-08-04-first-signing-validator.md).

Sections 2–6 preserve the sequence used to qualify the original node-only
slice. They are historical evidence, not commands to replay against the active
signing assignment. Current lifecycle changes must start from the live catalog
and must not use the non-signing workflow for the deposited identity.

At the start of that node-only exercise, the committed state was inert:

- `clusters/dev/node-apps.yaml` contains `suspend: true`;
- `assignment-ephemery-162-synthetic` is `stopped`;
- the three zonal Ethereum node groups are bounded at `0/1` and normally
  paused at zero; and
- no secret value is committed. The chart references only the Engine JWT
  interface in AWS Secrets Manager.

Kubernetes readiness in this slice is **API/process readiness**, measured by
TCP probes. It is not a synonym for chain sync. Likewise, a reconciled
HelmRelease, a provisioned LoadBalancer, or a zero Geth internal sync distance
is not independently a sync claim. The sustained evidence gates below are the
claim.

## 1. Confirm the selected generation is still usable

Ephemery resets approximately every four weeks. The repository pins generation
`162`; never interpret that filename as "latest". Before spending on capacity
or a Network Load Balancer, compare the catalog identity and artifact digest to
the maintained Ephemery release metadata. If a successor generation exists or
the generation-162 reset window has ended, stop and add the successor generation
through a reviewed PR. Do not edit `ephemery-162` in place and do not reuse its
PVCs.

The reviewed generation must still match all of these committed values:

```text
profile:        ephemery-162
generation:     162
chain/network:  39438162
artifact SHA:   478ca7181212f2d87137c337e854befbed8aacde8bee8f64d6ca7e28967ee2fb
PVC identity:   1607eeafd1831115cd81bfd3aed07ea9a154ec688776a25f3395c960756a048c
```

The successor policy is another reviewed generation-addressed profile and
assignment. A mutable `/latest/` artifact, an in-place digest edit, or deleting
old data by hand fails the lifecycle-identity contract.

## 2. Historical client/runtime preflight

The node-only exercise started from clean `main` and used the dedicated EKS
kubeconfig described in [`eks-capacity.md`](eks-capacity.md). The following
assertions describe that earlier stopped state; they intentionally fail against
the current signing catalog:

```bash
make check
make container-contracts
kubectl kustomize clusters/dev >/dev/null
kubectl kustomize platform/apps/nodes/dev >/dev/null

grep -F 'suspend: true' clusters/dev/node-apps.yaml
python3 tools/render_local_assignments.py \
  --values-for assignment-ephemery-162-synthetic \
  | grep -E 'lifecycleState: stopped|enabled: false|slashingProtectionConfirmed: false'
```

`make check` is offline after tool installation. `make container-contracts`
requires registry access for pinned images and HTTPS access to the
digest-verified Ephemery release; it runs the clients in isolated containers
and does not contact an Ethereum peer network.

`make container-contracts` runs both pinned client binaries under the explicit
UID/GID `1000:1000` used in the restricted EKS namespace. It also starts the
exact Geth image offline and proves that `chain_head_block`,
`chain_head_header`, and `p2p_peers` are real metrics before the dashboard uses
them. The same check starts two exact Lighthouse processes against the
digest-pinned Ephemery artifact in one isolated network namespace and proves
that `sync_peers_per_status`,
`beacon_head_state_slot`,
`beacon_head_state_finalized_epoch`, `slotclock_present_slot`, and
`slotclock_present_epoch` are emitted before recording rules depend on them.
The processes establish one local peer relationship, proving a non-zero metric
sample without contacting a public Ethereum network. Public-network peer health
remains an EKS runtime gate below.

The chart default remains Geth snap sync for permanent networks. The EKS
Ephemery overlay explicitly renders `--syncmode=full`: generation 162 is a
small, resetting chain, so replaying its bounded history is preferable to
carrying the snap-pivot recovery path observed in the first run. This setting
is a profile-specific mitigation, not evidence that every snap-sync restart
corrupts Geth and not proof that full sync survives interruption. Section 9
contains the runtime drill for that claim.

Stop if any rendered object contains a validator Deployment, a signing-key
reference, static AWS credentials, a NodePort outside Kubernetes allocation,
or a public port other than `30303/TCP+UDP`, `9000/TCP+UDP`, and `9001/UDP`.

## 3. Historical substrate check before starting a pair

Complete the controller/configuration stages of
[`eks-flux-bootstrap.md`](eks-flux-bootstrap.md). Keep the node layer and all
three signer layers suspended: `node-apps`, `signer-infrastructure-configs`,
`signer-prerequisites`, and `apps`.

```bash
flux get kustomizations -A
kubectl get storageclass ebs-gp3-encrypted
kubectl get clustersecretstore aws-engine-secrets
kubectl get nodes -L workload,eks.amazonaws.com/capacityType,topology.kubernetes.io/zone
kubectl get pods,statefulsets,persistentvolumeclaims -n ethereum
```

The expected Ethereum workload result is empty. The StorageClass must use
encrypted baseline `gp3`, `WaitForFirstConsumer`, expansion enabled, and no
default-class annotation. The restricted operator must have populated the
existing Engine JWT object with the expected `jwt.hex` property without
printing it, placing it in Terraform state, or copying it into Git. This
runbook does not create or retrieve that value.

The signer layers are an independent branch of the Flux graph. A node-only sync
does not require RDS or Web3Signer and does not remove any signer suspension.

## 4. Historical admission of the stopped node layer

Open and merge a reviewed PR changing only `clusters/dev/node-apps.yaml` to
`suspend: false`. Do not activate the assignment in the same PR.

```bash
flux reconcile kustomization node-apps --with-source
flux get kustomization node-apps
flux get helmrelease -n ethereum
kubectl get pvc -n ethereum \
  -l platform.galaxy-lab/assignment-id=assignment-ephemery-162-synthetic
```

Expected: one stopped HelmRelease and two `Pending` claims, execution `50Gi`
and consensus `20Gi`, both naming `ebs-gp3-encrypted`. With
`WaitForFirstConsumer`, Pending claims have no EBS volume and no storage charge
yet. The EKS-specific values file is a reset-aware cost hypothesis, not a
measurement; expand before exhaustion, never try to shrink it in place.

The EKS overlay admits only the Ephemery assignment. The Hoodi assignment and
every local-only dashboard remain outside this node layer.

The retained generation-162 execution claim was populated by the earlier snap
run. It cannot serve as evidence for a fresh full-sync start. Before the next
activation, while the assignment is stopped and no Pod exists, record both PVC
and PV identities and delete only the disposable execution claim. Flux/Helm
must recreate that same generation-addressed claim; retain the consensus claim
and its volume unchanged. This is a reviewed one-time test reset, not an
automatic recovery policy and never applies to validator keys or signer data.

## 5. Capacity procedure used by the qualification

For an unbound first run, choose one zone with available Spot capacity. For a
restart, use the same Availability Zone recorded on the bound PVs. The guarded
operator refuses a mismatch and refuses a second active Ethereum group.

```bash
export ETHEREUM_AZ="replace-with-reviewed-az"
test "$ETHEREUM_AZ" != "replace-with-reviewed-az"
./hack/eks-lab-capacity.sh status
./hack/eks-lab-capacity.sh resume --az "$ETHEREUM_AZ"
kubectl get nodes \
  -l workload=ethereum \
  -L eks.amazonaws.com/capacityType,topology.kubernetes.io/zone
```

The chart prefers `eks.amazonaws.com/capacityType=SPOT` but does not require it,
so an explicitly reviewed on-demand recovery remains possible. Hard isolation
comes from `workload=ethereum` plus the `dedicated=ethereum:NoSchedule` taint.
The two containers request 4 vCPU and 14 GiB total; their limits are bounded to
8 vCPU and 40 GiB, compatible with one nominal 8-vCPU/64-GiB lab worker.

A one-replica node pair has no redundancy for a PodDisruptionBudget to protect.
The EKS profile therefore omits that PDB instead of letting `minAvailable: 1`
block voluntary draining, and follows Amazon EKS guidance with a 30-second
termination grace period. Pods might still receive less of the interruption
window, so this is desired-state intent, not resilience evidence. A real
Spot interruption exercise must prove termination, rescheduling in the PV zone,
volume reattachment, and continued database integrity before recovery is
qualified. For a workload that requires guaranteed graceful termination, use
the reviewed on-demand recovery path instead.

Provider rationale: [Amazon EKS managed node groups](https://docs.aws.amazon.com/eks/latest/userguide/managed-node-groups.html)
recommends termination grace periods of 30 seconds or less for Spot workloads
and notes that Pods are not guaranteed to receive the full interruption window.

## 6. Historical node-only activation procedure

Before the assignment carried a signing identity, the lifecycle workflow was
used to request `activate` for
`assignment-ephemery-162-synthetic`, review the generated catalog and
projection diff, and merge it. The resulting HelmRelease must change to
`lifecycleState: active` while keeping `validator.enabled=false` and every
signing flag false.

After Flux reconciles:

```bash
flux reconcile kustomization node-apps --with-source
flux get helmrelease -n ethereum assignment-ephemery-162-synthetic
kubectl get externalsecret -n ethereum
kubectl get sts,pod,pvc,svc -n ethereum \
  -l platform.galaxy-lab/assignment-id=assignment-ephemery-162-synthetic
kubectl describe pod -n ethereum \
  -l platform.galaxy-lab/assignment-id=assignment-ephemery-162-synthetic
```

The ExternalSecret must become Ready without exposing its value. The Pod must
run as UID/GID 1000 under the restricted policy and land on the selected
Ethereum node. Both PVCs must bind in that same Availability Zone and their PV
node affinity must remain in that zone. The PVC names and annotations must
retain the generation-162 identity fingerprint, and the on-disk identity marker
must match it; dynamically provisioned PV names do not encode that fingerprint.

The internal Service exposes only beacon API and client metrics. Exactly one
selected pair has an internet-facing P2P Network Load Balancer; the other
client-diversity pairs retain ClusterIP P2P Services and use outbound peers.
The public Service selects AWS Load Balancer Controller explicitly through
`loadBalancerClass: service.k8s.aws/nlb`, uses Pod-IP targets, and health-checks
the pair's consensus TCP listener on port 9000. Its source range is public
because Ethereum P2P is public. JSON-RPC, Engine API, beacon API, and metrics
are not ports on that LoadBalancer.

The chart fails Helm rendering when that AWS load-balancer class is paired
with instance targets, omits
`aws-load-balancer-enable-tcp-udp-listener=true`, or reintroduces the legacy
`aws-load-balancer-type` annotation. This keeps the five fixed P2P ports on one
controller-owned mixed-protocol Service instead of silently dropping UDP or
falling back to the in-tree reconciler. `node-apps` also names
`infrastructure-controllers` as a direct Flux dependency, so the Service is not
submitted before the controller layer reports Ready.

```bash
kubectl rollout status -n kube-system \
  deployment/aws-load-balancer-controller --timeout=5m
kubectl get service -n ethereum pair-ephemery-162-synthetic-p2p-nlb \
  -o jsonpath='{.spec.type}{"\n"}{.spec.loadBalancerClass}{"\n"}{.status.loadBalancer.ingress[0].hostname}{"\n"}'
kubectl get endpointslice -n ethereum \
  -l kubernetes.io/service-name=pair-ephemery-162-synthetic-p2p-nlb

kubectl get service -n ethereum -o json | jq -e '
  [.items[] | select(.spec.type == "LoadBalancer")
    | select(.spec.loadBalancerClass == "service.k8s.aws/nlb")] | length == 1
' >/dev/null
```

A hostname and healthy endpoints prove only the Kubernetes/controller path.
Before claiming the NLB is qualified, inspect its AWS listeners and target
health and exercise both TCP and UDP from outside the VPC. Those checks still
do not prove that either client advertises the public address in its ENR;
observe attributable inbound peer traffic separately before claiming
bidirectional discovery. The one NLB has a standing hourly cost. Returning its
selected assignment to stopped removes the Service and therefore the NLB;
resuming the assignment creates a new hostname without changing chain-data
identity.

Observed on 2026-08-05 for the selected Geth + Lighthouse pair:

- one internet-facing NLB owned all public P2P listeners;
- AWS rendered `TCP_UDP` listeners on 30303 and 9000 and a UDP listener on
  9001;
- each listener's Pod-IP target was healthy;
- external TCP connections reached 30303 and 9000; and
- VPC Flow Logs recorded accepted bounded external UDP probes delivered to the
  Pod IP on 30303 and 9000; and
- the LoadBalancer Service exposed no JSON-RPC, Engine API, beacon API, signer,
  or metrics port.

This checkpoint qualifies the controller, listener, target, external TCP path,
and bounded UDP network traversal on the two combined listeners. A malformed
probe does not prove an Ethereum discovery exchange. It does not claim valid
inbound peer attribution, 9001 UDP traffic, client-advertised public addresses,
or the stop/delete/resume lifecycle. Keep those fields unknown until separately
observed.

## 7. Verify exact chain identity

The artifact init path verifies the archive SHA before Geth genesis
initialization. Still verify the running clients rather than inferring identity
from the manifest:

```bash
kubectl exec -n ethereum sts/pair-ephemery-162-synthetic \
  -c execution -- geth attach --exec 'eth.chainId' /data/geth.ipc
kubectl exec -n ethereum sts/pair-ephemery-162-synthetic \
  -c execution -- geth attach --exec 'eth.getBlock(0).hash' /data/geth.ipc

kubectl port-forward -n ethereum \
  service/pair-ephemery-162-synthetic 5052:5052
# In another terminal:
curl -fsS http://127.0.0.1:5052/eth/v1/beacon/genesis | jq .
curl -fsS http://127.0.0.1:5052/eth/v1/node/syncing | jq .
```

The EL chain ID and block-zero hash, plus CL genesis time and validators root,
must exactly match the committed profile. Stop on any mismatch; never "repair"
one client to match the other on a mounted volume.

## 8. Collect sustained sync evidence

Open Grafana's **Ethereum Platform / EKS Ephemery sync evidence** dashboard.
Use at least one continuous 15-minute observation window after both metrics
targets appear. Record screenshots or query results for all of the following:

1. both scrape targets remain up with no restart loop;
2. execution and consensus peer counts are non-zero;
3. `validator_platform_execution_head_block` and
   `validator_platform_execution_head_changes_15m` advance;
4. `validator_platform_consensus_head_changes_15m` advances;
5. Geth internal sync distance trends toward and then remains at zero;
6. Lighthouse slot lag trends toward the head and finality lag converges;
7. PVC consumption remains below the reviewed expansion threshold; and
8. the network profile, generation, identity fingerprint, assignment, clients,
   cluster, environment, and lifecycle labels match the intended pair.

The internal sync distance is `chain_head_header - chain_head_block`. It can be
zero at genesis, on a stale isolated node, or at the actual network head. It is
therefore never accepted without peers, head changes, CL progress, and exact
identity evidence. Likewise, a TCP readiness probe says the API answers—it says
nothing about the head.

This node-only milestone did not authorize validator duties. Key generation,
deposit, signer binding, slashing storage, doppelganger protection, uniqueness,
and duty activation were reviewed separately.

## 9. Stop and resume without losing chain identity

For a non-signing assignment, request `stop` through the lifecycle workflow and
wait for Flux/Helm to remove
the StatefulSet, ExternalSecret, Services, and P2P LoadBalancer. Confirm the two
PVCs remain Bound, then pause the worker in the PV's zone:

```bash
kubectl get pods -n ethereum \
  -l platform.galaxy-lab/assignment-id=assignment-ephemery-162-synthetic
kubectl get pvc,pv -n ethereum
./hack/eks-lab-capacity.sh pause --az "$ETHEREUM_AZ"
```

Stopped retains the encrypted gp3 volumes and their storage cost. Resume the
same Availability Zone, reactivate through another reviewed PR, and prove that
the same PVC/PV identities reattach before collecting recovery evidence.

The [2026-08-05 Spot reschedule](../evidence/2026-08-05-eks-spot-rebalance.md)
reused this pair's claims and resumed head progression, but it did not capture
the execution client's shutdown sequence. It therefore does not prove a
graceful Geth restart and does not complete #84.

During a fresh full-sync qualification run, perform one deliberate graceful
restart while the execution head is advancing. Preserve the deleted Pod's log
stream outside the repository; `kubectl logs --previous` on the replacement
Pod cannot retrieve logs from a different Pod UID. This is a disruptive
testnet runtime experiment: obtain operator approval and choose a window that
accepts possible validator-duty downtime before running it. It was not run by
the repository-only change that added these checks.

```bash
EVIDENCE_DIR="$(mktemp -d)"
POD=pair-ephemery-162-synthetic-0

kubectl get pvc -n ethereum -o custom-columns=NAME:.metadata.name,UID:.metadata.uid,VOLUME:.spec.volumeName
CAPTURE_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
kubectl logs -n ethereum "$POD" -c execution --timestamps --follow \
  --since-time="$CAPTURE_STARTED_AT" \
  >"$EVIDENCE_DIR/geth-shutdown.log" 2>&1 &
GETH_LOG_PID=$!
sleep 2
kill -0 "$GETH_LOG_PID"

kubectl delete pod -n ethereum "$POD"
wait "$GETH_LOG_PID" || true

grep -F 'Got interrupt, shutting down' "$EVIDENCE_DIR/geth-shutdown.log"
grep -F 'Ethereum protocol stopped' "$EVIDENCE_DIR/geth-shutdown.log"
grep -F 'Blockchain stopped' "$EVIDENCE_DIR/geth-shutdown.log"
kubectl wait -n ethereum --for=condition=Ready \
  "pod/$POD" --timeout=15m
kubectl logs -n ethereum "$POD" -c execution --since-time="$CAPTURE_STARTED_AT" \
  >"$EVIDENCE_DIR/geth-restart.log"
if grep -Eq 'missing trie node|Fatal: Failed to register the Ethereum service' \
  "$EVIDENCE_DIR/geth-restart.log"; then
  printf 'Geth did not recover from the retained execution claim.\n' >&2
  exit 1
fi
```

Do not use `--force` or override the 30-second grace period. The three shutdown
markers must occur after log capture begins and before the old Pod disappears;
otherwise the run has not demonstrated a graceful Geth stop. After the Pod is
Ready, prove both PVC UIDs and PV names are unchanged, verify exact chain
identity again, and observe both heads resume advancing for the sustained
15-minute window in section 8. A missing shutdown marker, missing-trie error,
claim replacement, identity mismatch, or stalled head fails the drill and
leaves #84 open. One passing Ephemery drill qualifies this profile only; it is
not a general durability claim for Geth, EBS, Spot interruption, or snap sync.

When generation 162 is retired, `archived` removes its reproducible chain-data
claims and the `Delete` reclaim policy releases the EBS volumes. Add the
successor generation first and never mount the archived fingerprint under the
new profile.
