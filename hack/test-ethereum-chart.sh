#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly CHART="${REPOSITORY_ROOT}/charts/ethereum-node"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "${temporary_directory}"' EXIT

helm lint "${CHART}"
helm template ethereum-node "${CHART}" --namespace ethereum \
  >"${temporary_directory}/stopped.yaml"
helm template ethereum-node "${CHART}" --namespace ethereum \
  --set lifecycleState=active \
  >"${temporary_directory}/active.yaml"
python3 "${REPOSITORY_ROOT}/tools/render_local_assignments.py" \
  --values-for assignment-ephemery-162-synthetic \
  | helm template ephemery-162 "${CHART}" --namespace ethereum \
      --values - \
      --set lifecycleState=active \
      >"${temporary_directory}/ephemery-162.yaml"
python3 "${REPOSITORY_ROOT}/tools/render_local_assignments.py" \
  --values-for assignment-ephemery-162-synthetic \
  >"${temporary_directory}/ephemery-eks-projected-values.yaml"
python3 - "${temporary_directory}/ephemery-eks-projected-values.yaml" <<'PY'
import sys

import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    values = yaml.safe_load(stream)
values["p2p"] = {
    "service": {
        "enabled": True,
        "nameSuffix": "p2p-nlb",
        "type": "LoadBalancer",
        "loadBalancerClass": "service.k8s.aws/nlb",
        "annotations": {
            "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type": "ip",
            "service.beta.kubernetes.io/aws-load-balancer-enable-tcp-udp-listener": "true",
            "service.beta.kubernetes.io/aws-load-balancer-scheme": "internet-facing",
            "service.beta.kubernetes.io/aws-load-balancer-healthcheck-protocol": "tcp",
            "service.beta.kubernetes.io/aws-load-balancer-healthcheck-port": "9000",
            "service.beta.kubernetes.io/aws-load-balancer-attributes": "load_balancing.cross_zone.enabled=true",
        },
        "externalTrafficPolicy": "Cluster",
        "loadBalancerSourceRanges": ["0.0.0.0/0"],
    }
}
with open(path, "w", encoding="utf-8") as stream:
    yaml.safe_dump(values, stream, sort_keys=False)
PY
helm template ephemery-eks "${CHART}" --namespace ethereum \
  --values "${CHART}/values-eks-ephemery.yaml" \
  --values "${temporary_directory}/ephemery-eks-projected-values.yaml" \
  --set lifecycleState=active \
  --set telemetry.cluster=eth-validator-platform-dev \
  --set telemetry.environment=dev \
  >"${temporary_directory}/ephemery-eks.yaml"
helm template ethereum-node "${CHART}" --namespace ethereum \
  --set lifecycleState=active \
  --set validator.enabled=true \
  --set validator.slashingProtectionConfirmed=true \
  --set networkProfile.signer.web3signer.signingQualified=true \
  --set validator.publicKey=0x111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111 \
  --set validator.feeRecipient=0x2222222222222222222222222222222222222222 \
  >"${temporary_directory}/signing.yaml"

helm template ethereum-node "${CHART}" --namespace ethereum \
  --values "${CHART}/values-eks-hoodi-storage.yaml" \
  --set lifecycleState=active \
  --set telemetry.cluster=eks-eth-validator-dev \
  --set telemetry.environment=dev \
  --set validator.enabled=true \
  --set validator.slashingProtectionConfirmed=true \
  --set networkProfile.signer.web3signer.signingQualified=true \
  --set validator.publicKey=0x111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111 \
  --set validator.feeRecipient=0x2222222222222222222222222222222222222222 \
  >"${temporary_directory}/eks-storage.yaml"

if grep -Eq '^kind: (Deployment|StatefulSet|ExternalSecret)$' "${temporary_directory}/stopped.yaml"; then
  printf 'Stopped profile unexpectedly renders running compute or secret projection.\n' >&2
  exit 1
fi
grep -q 'platform.galaxy-lab/signing-enabled: "false"' "${temporary_directory}/stopped.yaml"

grep -q '^kind: StatefulSet$' "${temporary_directory}/active.yaml"
grep -q -- '--hoodi' "${temporary_directory}/active.yaml"
grep -q -- '--network=hoodi' "${temporary_directory}/active.yaml"
grep -q 'networkProfile: "hoodi"' "${temporary_directory}/active.yaml"
grep -q 'networkGeneration: "permanent"' "${temporary_directory}/active.yaml"
grep -q 'networkIdentity: "c7484b91cd367b3b99a8e6dbfc92fe637ee435fa8a4d55cc5904777bf0e574b2"' "${temporary_directory}/active.yaml"
grep -q 'path: /debug/metrics/prometheus' "${temporary_directory}/active.yaml"
grep -q 'record: validator_platform_consensus_slot_lag' "${temporary_directory}/active.yaml"
grep -q '@sha256:' "${temporary_directory}/active.yaml"
if grep -q '^kind: Deployment$' "${temporary_directory}/active.yaml"; then
  printf 'Non-signing active profile unexpectedly renders a validator client.\n' >&2
  exit 1
fi

grep -q '^kind: StatefulSet$' "${temporary_directory}/ephemery-162.yaml"
grep -q 'name: fetch-network-artifacts' "${temporary_directory}/ephemery-162.yaml"
grep -q 'name: verify-network-artifacts' "${temporary_directory}/ephemery-162.yaml"
grep -q 'name: initialize-geth-genesis' "${temporary_directory}/ephemery-162.yaml"
grep -q '478ca7181212f2d87137c337e854befbed8aacde8bee8f64d6ca7e28967ee2fb' "${temporary_directory}/ephemery-162.yaml"
grep -q -- '--networkid=39438162' "${temporary_directory}/ephemery-162.yaml"
grep -q -- '--testnet-dir=/network/files' "${temporary_directory}/ephemery-162.yaml"
grep -q -- '--checkpoint-sync-url=https://checkpoint-sync.ephemery.ethpandaops.io/' "${temporary_directory}/ephemery-162.yaml"
grep -q 'command: \[/bin/sh, -ec\]' "${temporary_directory}/ephemery-162.yaml"
grep -q 'paste -sd, "/network/files/boot_enr.txt"' "${temporary_directory}/ephemery-162.yaml"
grep -q -- '--boot-nodes="$bootnodes"' "${temporary_directory}/ephemery-162.yaml"
grep -q 'deposit_contract_block.txt' "${temporary_directory}/ephemery-162.yaml"
grep -q 'pair-ephemery-162-sy-eaa1dc641654bfe3-1607eeafd183-execution' "${temporary_directory}/ephemery-162.yaml"
grep -q 'pair-ephemery-162-sy-eaa1dc641654bfe3-1607eeafd183-consensus' "${temporary_directory}/ephemery-162.yaml"
if grep -Eq -- '--hoodi|--network=ephemery' "${temporary_directory}/ephemery-162.yaml"; then
  printf 'Ephemery render inherited a built-in network flag.\n' >&2
  exit 1
fi
grep -q '^kind: Deployment$' "${temporary_directory}/ephemery-162.yaml"
grep -q -- '--testnet-dir=/validator-network' "${temporary_directory}/ephemery-162.yaml"
grep -q -- '--enable-doppelganger-protection' "${temporary_directory}/ephemery-162.yaml"
grep -q 'http://web3signer.signing.svc.cluster.local:9000' "${temporary_directory}/ephemery-162.yaml"
if grep -Eq 'validator-keystore|signingSecretRef' "${temporary_directory}/ephemery-162.yaml"; then
  printf 'Ephemery pair chart must not project validator key material.\n' >&2
  exit 1
fi

grep -q '^ *type: LoadBalancer$' "${temporary_directory}/ephemery-eks.yaml"
grep -q '^ *loadBalancerClass: "service.k8s.aws/nlb"$' "${temporary_directory}/ephemery-eks.yaml"
grep -q 'service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip' "${temporary_directory}/ephemery-eks.yaml"
grep -q '^ *externalTrafficPolicy: Cluster$' "${temporary_directory}/ephemery-eks.yaml"
if grep -q 'service.beta.kubernetes.io/aws-load-balancer-type:' "${temporary_directory}/ephemery-eks.yaml"; then
  printf 'EKS P2P Service still requests the legacy AWS cloud controller.\n' >&2
  exit 1
fi
grep -q 'eks.amazonaws.com/capacityType' "${temporary_directory}/ephemery-eks.yaml"
grep -q '^ *values: \[SPOT\]$' "${temporary_directory}/ephemery-eks.yaml"
if grep -q 'nodePort:' "${temporary_directory}/ephemery-eks.yaml"; then
  printf 'EKS P2P Service must let Kubernetes allocate a valid NodePort.\n' >&2
  exit 1
fi
ephemery_eks_class_references="$(grep -c '^ *storageClassName: ebs-gp3-encrypted$' "${temporary_directory}/ephemery-eks.yaml" || true)"
if [[ "${ephemery_eks_class_references}" -ne 3 ]]; then
  printf 'Expected execution, consensus, and validator Ephemery claims on encrypted gp3; found %s.\n' \
    "${ephemery_eks_class_references}" >&2
  exit 1
fi
for expected_size in 50Gi 20Gi 5Gi; do
  grep -q "storage: ${expected_size}$" "${temporary_directory}/ephemery-eks.yaml"
done
grep -q '^kind: Deployment$' "${temporary_directory}/ephemery-eks.yaml"
grep -q 'platform.galaxy-lab/signing-enabled: "true"' "${temporary_directory}/ephemery-eks.yaml"
if python3 "${REPOSITORY_ROOT}/tools/render_local_assignments.py" \
  --values-for assignment-ephemery-162-synthetic \
  | helm template ephemery-162 "${CHART}" --namespace ethereum \
      --values - \
      --set lifecycleState=active \
      --set networkProfile.signer.web3signer.signingQualified=false \
      >"${temporary_directory}/unsafe-ephemery.yaml" 2>/dev/null; then
  printf 'Ephemery rendered validator duties after signer qualification was removed.\n' >&2
  exit 1
fi

grep -q '^kind: StatefulSet$' "${temporary_directory}/signing.yaml"
grep -q '^kind: Deployment$' "${temporary_directory}/signing.yaml"
grep -q 'http://web3signer.signing.svc.cluster.local:9000' "${temporary_directory}/signing.yaml"
grep -q 'platform.galaxy-lab/signing-enabled: "true"' "${temporary_directory}/signing.yaml"
if grep -Eq 'validator-key|web3signer-db|platform.galaxy-lab/component: web3signer' "${temporary_directory}/signing.yaml"; then
  printf 'Pair chart must not mount signing keys, database credentials, or deploy Web3Signer.\n' >&2
  exit 1
fi

# Every claim in the EKS profile must name the encrypted gp3 class explicitly.
# Inheriting `standard` would leave the claim permanently unbound on EKS;
# inheriting the EKS-created legacy `gp2` class would be the cost regression
# the class exists to avoid.
if grep -Eq '^ *storageClassName: (standard|gp2)$' "${temporary_directory}/eks-storage.yaml"; then
  printf 'EKS storage profile inherited the local standard class or the legacy gp2 class.\n' >&2
  exit 1
fi
eks_class_references="$(grep -c '^ *storageClassName: ebs-gp3-encrypted$' "${temporary_directory}/eks-storage.yaml" || true)"
if [[ "${eks_class_references}" -ne 3 ]]; then
  printf 'Expected the execution, consensus, and validator claims to name the EKS gp3 class; found %s.\n' \
    "${eks_class_references}" >&2
  exit 1
fi
for expected_size in 200Gi 50Gi 5Gi; do
  grep -q "storage: ${expected_size}$" "${temporary_directory}/eks-storage.yaml"
done
# The EKS profile stays opt-in: the chart's own default remains the local class,
# so no local render silently references an EBS-only class.
grep -q '^ *storageClassName: standard$' "${temporary_directory}/stopped.yaml"

if helm template ethereum-node "${CHART}" --namespace ethereum \
  --set lifecycleState=active \
  --set validator.enabled=true \
  --set networkProfile.signer.web3signer.signingQualified=true \
  --set validator.publicKey=0x111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111 \
  --set validator.feeRecipient=0x2222222222222222222222222222222222222222 \
  >"${temporary_directory}/unsafe.yaml" 2>/dev/null; then
  printf 'Signing profile rendered without the slashing-protection acknowledgment.\n' >&2
  exit 1
fi

printf 'Validated stopped, Hoodi, qualified Ephemery signing, EKS storage, and unsafe chart profiles.\n'
