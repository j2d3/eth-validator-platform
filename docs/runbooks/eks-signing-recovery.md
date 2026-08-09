# EKS signing recovery

This runbook describes the post-cold-standby path from restored AWS resources
to signing activity for the Ephemery testnet. It deliberately separates
mechanical recovery from signing authorization.

## What survives cold standby

- encrypted validator keystore containers remain in AWS Secrets Manager;
- the RDS final snapshot contains Web3Signer slashing-protection history;
- Terraform state and the recovery manifest remain in the protected S3 state
  bucket; and
- validator deposits and withdrawal credentials remain on the testnet.

The EKS cluster, workers, RDS instance, Flux deploy key, Pods, PVC attachments,
and load balancers do not survive. Recovery recreates those resources.

## Recovery modes

The trusted-local cold-standby operator restores EKS and RDS. Flux is then
bootstrapped with a newly generated read-only deploy key and the Terraform
outputs required by the AWS adapters. The deploy key is removed again during
teardown.

After Flux is ready, run the non-mutating gate runner:

```bash
export KUBECONFIG="$PWD/.local/eks-kubeconfig"
./hack/eks-signing-recovery.sh observe
```

`observe` proves that EKS and RDS are available, the infrastructure and signer
Kustomizations are Ready, durable secret containers exist, and no lifecycle
record is already signing. It does not read secret values, start workloads, or
change Git.

The testnet signing gate adds an explicit operator confirmation:

```bash
RECOVERY_CONFIRMATION=ephemery-testnet \
  ./hack/eks-signing-recovery.sh signing
```

This still does not edit the catalog or enable a validator. It proves that the
restored substrate is ready for the final reviewed GitOps activation.

## Final activation contract

The activation change must identify the intended assignments and set, together:

1. the assignment lifecycle to `active`;
2. `validator.enabled: true`;
3. `signingEnabled: true`;
4. `slashingProtectionConfirmed: true`;
5. `doppelgangerProtectionConfirmed: true`; and
6. the exact registered identity and node-pair references.

Before merging that change, the operator must verify chain identity, decreasing
sync distance, finalized-head progress, exact Web3Signer public-key inventory,
RDS schema/row continuity, one-active-assignment uniqueness, and doppelganger
clearance. The activation PR is the durable authorization record; a Pod being
Ready is not authorization to sign.

## Automation boundary

The recovery runner can be called by a trusted-local wrapper after Terraform
restore and Flux bootstrap. It is intentionally not a GitHub Actions apply
workflow and it does not bypass Flux with `kubectl apply`. A future testnet
controller may open the activation PR automatically after the same gates, but
the merge must remain the explicit authorization boundary until that controller
has been separately qualified.

## Cold teardown after the exercise

Stop signing through a reviewed GitOps change, verify no signing-enabled
lifecycle records or Ethereum Pods remain, capture a fresh encrypted final RDS
snapshot, and invoke the guarded cold-standby `down` operation. Never delete
Secrets Manager containers or the final snapshot as part of the exercise.
