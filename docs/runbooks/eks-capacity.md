# EKS Ethereum capacity operations

`hack/eks-lab-capacity.sh` is the bounded operator interface for the single EKS
development lab. Terraform owns the cluster, managed-node-group definitions,
zonal subnets, instance pools, taints, labels, launch templates, and the 0/1
capacity bounds. The pinned EKS Terraform module intentionally ignores a
managed group's live `desired_size` after creation. This command owns only the
operational transition between zero and one Ethereum worker.

It is not a validator lifecycle controller. It never edits Git, Flux,
HelmReleases, validator assignments, secrets, Web3Signer, or signing state.

## Dedicated context

The default context is deliberately not the user's global Kubernetes context:

```text
cluster:    eth-validator-platform-dev
region:     us-west-2
profile:    default
kubeconfig: .local/eks-kubeconfig
```

Every mutating command compares the kubeconfig API endpoint with the endpoint
AWS reports for the named cluster. A GKE context or a kubeconfig for another EKS
cluster therefore fails before capacity changes.

Override defaults explicitly when needed:

```bash
./hack/eks-lab-capacity.sh status \
  --cluster eth-validator-platform-dev \
  --region us-west-2 \
  --profile default \
  --kubeconfig .local/eks-kubeconfig
```

## Status

```bash
make eks-capacity-status
# or
./hack/eks-lab-capacity.sh status
```

Status discovers groups from EKS and selects the ones carrying
`workload=ethereum`; it does not infer live desired size from Terraform. It
shows each group's Availability Zone, name, capacity type, health state, bounds,
and desired size, followed by Ethereum nodes and platform lifecycle/PVC records.
Enumeration is atomic: if EKS cannot list the groups or describe any one of
them, the command exits non-zero before printing a partial fleet table.

## Pause

First merge the assignment to a stopped state and wait for Flux/Helm to remove
the execution, consensus, and validator-client pods. Then name the exact zone:

```bash
./hack/eks-lab-capacity.sh pause --az us-west-2a
```

The command refuses the update unless all of these are true:

- the kubeconfig endpoint matches the named EKS cluster;
- exactly one healthy Ethereum managed group exists for the requested zone;
- its declared bounds are still `min=0,max=1`;
- no other Ethereum group has non-zero desired capacity;
- no lifecycle record reports signing enabled;
- every lifecycle record is `stopped` or `archived`;
- no execution/consensus pair pod or validator-client pod exists;
- platform PVCs are not orphaned from every lifecycle record.

It then calls EKS `UpdateNodegroupConfig`, waits for the update to succeed, and
waits until the group's Kubernetes Node is absent. Calling pause again is
idempotent, but it still evaluates the safety guards before reporting that the
group is already at zero.

Pause terminates the disposable EC2 worker and its root disk. Application EBS
PVCs, AWS Secrets Manager values, RDS slashing history, node-group definitions,
and validator identity are outside that root disk and remain. The current bare
foundation has none of the application resources yet, so its first manual pause
removed only an unused Spot worker.

## Resume

Resume capacity while the assignment remains stopped and signing remains
disabled:

```bash
./hack/eks-lab-capacity.sh resume --az us-west-2a
```

Resume applies the same lifecycle/signing/pod guards. It additionally inspects
every bound platform PVC's PersistentVolume node affinity. If an EBS volume is
bound in `us-west-2b`, requesting `us-west-2a` fails with the correct zone in the
error; the command never creates useless capacity beside an unattachable disk.
Pending `WaitForFirstConsumer` claims have no bound zone yet and do not block the
operator's initial choice.

After verifying that every other Ethereum group is at zero, the command requests
desired size one and waits for one Ready Kubernetes Node. It does not reconcile
or activate the pair. The separate Flux lifecycle change happens only after the
node, volume attachment, clients, signer, network identity, sync, and activation
gates are ready.

## Observed qualification

On 2026-08-02 the reviewed Spot/zonal Terraform apply created all three groups.
The initially selected `us-west-2a` group obtained a Ready `r8i.2xlarge` Spot
worker. With no application pods or PVCs present, an EKS API update then changed
that group from desired one to zero and the worker disappeared. A Terraform plan
afterward reported no changes, proving the intended ownership boundary: the
operator controls desired size without manufacturing Terraform drift.

Behavioral tests use fake AWS and Kubernetes endpoints to prove that signing,
active lifecycle, remaining pods, a second active zone, and a wrong-zone EBS PV
all prevent the mutation.

## Cost boundary

Zero Ethereum workers does not mean a zero-cost environment. The EKS control
plane, two system nodes, NAT gateway, root disks, logs, and later RDS/application
volumes continue billing. Destroying those belongs to the separately reviewed
Terraform teardown, not to this small operational command.
