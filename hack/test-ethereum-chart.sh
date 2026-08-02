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
helm template ethereum-node "${CHART}" --namespace ethereum \
  --set lifecycleState=active \
  --set validator.enabled=true \
  --set validator.slashingProtectionConfirmed=true \
  --set validator.publicKey=0x111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111 \
  --set validator.feeRecipient=0x2222222222222222222222222222222222222222 \
  >"${temporary_directory}/signing.yaml"

if grep -Eq '^kind: (Deployment|StatefulSet|ExternalSecret)$' "${temporary_directory}/stopped.yaml"; then
  printf 'Stopped profile unexpectedly renders running compute or secret projection.\n' >&2
  exit 1
fi

grep -q '^kind: StatefulSet$' "${temporary_directory}/active.yaml"
grep -q 'path: /debug/metrics/prometheus' "${temporary_directory}/active.yaml"
grep -q 'record: validator_platform_consensus_slot_lag' "${temporary_directory}/active.yaml"
grep -q '@sha256:' "${temporary_directory}/active.yaml"
if grep -q '^kind: Deployment$' "${temporary_directory}/active.yaml"; then
  printf 'Non-signing active profile unexpectedly renders a validator client.\n' >&2
  exit 1
fi

grep -q '^kind: StatefulSet$' "${temporary_directory}/signing.yaml"
grep -q '^kind: Deployment$' "${temporary_directory}/signing.yaml"
grep -q 'http://web3signer.signing.svc.cluster.local:9000' "${temporary_directory}/signing.yaml"
if grep -Eq 'validator-key|web3signer-db|platform.galaxy-lab/component: web3signer' "${temporary_directory}/signing.yaml"; then
  printf 'Pair chart must not mount signing keys, database credentials, or deploy Web3Signer.\n' >&2
  exit 1
fi

if helm template ethereum-node "${CHART}" --namespace ethereum \
  --set lifecycleState=active \
  --set validator.enabled=true \
  --set validator.publicKey=0x111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111 \
  --set validator.feeRecipient=0x2222222222222222222222222222222222222222 \
  >"${temporary_directory}/unsafe.yaml" 2>/dev/null; then
  printf 'Signing profile rendered without the slashing-protection acknowledgment.\n' >&2
  exit 1
fi

printf 'Validated stopped, active non-signing, signing, and unsafe chart profiles.\n'
