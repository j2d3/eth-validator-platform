#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SECRET_DIRECTORY="${REPOSITORY_ROOT}/secrets/local"
readonly JWT_FILE="${SECRET_DIRECTORY}/engine-jwt.hex"
readonly VALIDATOR_DIRECTORY="${SECRET_DIRECTORY}/validator"
readonly LOCAL_KUBECONFIG="${REPOSITORY_ROOT}/.local/kubeconfig"

export KUBECONFIG="${KUBECONFIG:-${LOCAL_KUBECONFIG}}"

if [[ ! -f "${KUBECONFIG}" ]]; then
  printf 'Local kubeconfig not found at %s. Create the kind cluster first.\n' "${KUBECONFIG}" >&2
  exit 1
fi

if ! kubectl get namespace platform-secrets >/dev/null 2>&1; then
  printf 'The Flux-managed platform-secrets namespace is not ready. Check: flux get all -A\n' >&2
  exit 1
fi

umask 077
mkdir -p "${SECRET_DIRECTORY}"

if [[ ! -f "${JWT_FILE}" ]]; then
  openssl rand -hex 32 >"${JWT_FILE}"
  printf 'Generated a local Engine API JWT at %s. It is excluded from Git.\n' "${JWT_FILE}"
fi

kubectl create secret generic engine-jwt \
  --namespace platform-secrets \
  --from-file=jwt.hex="${JWT_FILE}" \
  --dry-run=client \
  --output=yaml | kubectl apply -f -
kubectl label secret engine-jwt \
  --namespace platform-secrets \
  platform.galaxy-lab/source=local-bootstrap \
  --overwrite >/dev/null

if [[ -f "${VALIDATOR_DIRECTORY}/keystore.json" && -f "${VALIDATOR_DIRECTORY}/password.txt" ]]; then
  kubectl create secret generic validator-keystore \
    --namespace platform-secrets \
    --from-file=keystore.json="${VALIDATOR_DIRECTORY}/keystore.json" \
    --from-file=password.txt="${VALIDATOR_DIRECTORY}/password.txt" \
    --dry-run=client \
    --output=yaml | kubectl apply -f -
  kubectl label secret validator-keystore \
    --namespace platform-secrets \
    platform.galaxy-lab/source=local-bootstrap \
    --overwrite >/dev/null
  printf 'Seeded the optional encrypted validator keystore. This does not enable signing.\n'
else
  printf 'No optional validator keystore found; platform-smoke mode remains non-signing.\n'
fi
