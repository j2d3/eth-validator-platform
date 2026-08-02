#!/usr/bin/env bash
set -euo pipefail

readonly CLUSTER_NAME="eth-validator-local"
readonly KIND_NODE_IMAGE="kindest/node:v1.35.5@sha256:ce977ae6d65918d0b58a5f8b5e940429c2ce42fa3a5619ec2bbc60b949c0ac95"
readonly GITHUB_OWNER="j2d3"
readonly GITHUB_REPOSITORY="eth-validator-platform"
readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOCAL_KUBECONFIG="${REPOSITORY_ROOT}/.local/kubeconfig"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

cluster_exists() {
  kind get clusters 2>/dev/null | grep -Fxq "${CLUSTER_NAME}"
}

preflight() {
  local command_name
  local available_kib
  local required_kib
  for command_name in docker kind kubectl flux git gh jq openssl; do
    require_command "${command_name}"
  done

  available_kib="$(df -Pk "${REPOSITORY_ROOT}" | awk 'NR == 2 {print $4}')"
  required_kib=$((40 * 1024 * 1024))
  if (( available_kib < required_kib )); then
    printf 'Insufficient free disk for platform-smoke: %.1f GiB available; at least 40 GiB required.\n' \
      "$(awk -v kib="${available_kib}" 'BEGIN {printf "%.1f", kib / 1024 / 1024}')" >&2
    exit 1
  fi

  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop or Colima.\n' >&2
    exit 1
  fi

  printf 'Docker: %s\n' "$(docker version --format '{{.Server.Version}}')"
  printf 'kind: %s\n' "$(kind version)"
  printf 'kubectl: %s\n' "$(kubectl version --client -o json | jq -r '.clientVersion.gitVersion')"
  printf 'Flux: %s\n' "$(flux --version)"
}

up() {
  preflight
  if cluster_exists; then
    printf 'Cluster %s already exists; leaving it unchanged.\n' "${CLUSTER_NAME}"
    kind export kubeconfig --name "${CLUSTER_NAME}" --kubeconfig "${LOCAL_KUBECONFIG}" >/dev/null
    kubectl --kubeconfig "${LOCAL_KUBECONFIG}" cluster-info
    return
  fi

  mkdir -p "$(dirname "${LOCAL_KUBECONFIG}")"
  kind create cluster \
    --name "${CLUSTER_NAME}" \
    --image "${KIND_NODE_IMAGE}" \
    --config "${REPOSITORY_ROOT}/hack/kind.yaml" \
    --kubeconfig "${LOCAL_KUBECONFIG}" \
    --wait 5m

  kubectl --kubeconfig "${LOCAL_KUBECONFIG}" wait --for=condition=Ready node --all --timeout=2m
  printf 'Local Kubernetes foundation is ready at %s. Flux has not been bootstrapped yet.\n' "${LOCAL_KUBECONFIG}"
}

github_token() {
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    return
  fi

  if ! GITHUB_TOKEN="$(gh auth token -h github.com -u "${GITHUB_OWNER}" 2>/dev/null)"; then
    printf 'No valid GitHub token is available for %s. Re-authenticate gh, then retry.\n' "${GITHUB_OWNER}" >&2
    exit 1
  fi
  export GITHUB_TOKEN
}

bootstrap() {
  preflight
  if ! cluster_exists; then
    printf 'Create the local cluster first with: make local-up\n' >&2
    exit 1
  fi

  if [[ -n "$(git -C "${REPOSITORY_ROOT}" status --porcelain)" ]]; then
    printf 'Flux bootstrap requires a clean worktree whose commit is pushed to GitHub.\n' >&2
    exit 1
  fi

  github_token
  flux check --pre --kubeconfig="${LOCAL_KUBECONFIG}"
  flux bootstrap github \
    --owner="${GITHUB_OWNER}" \
    --repository="${GITHUB_REPOSITORY}" \
    --branch=main \
    --path=clusters/local \
    --personal \
    --private \
    --network-policy=true \
    --kubeconfig="${LOCAL_KUBECONFIG}" \
    --version=v2.8.8
}

seed() {
  if ! cluster_exists; then
    printf 'Create and bootstrap the local cluster before seeding secrets.\n' >&2
    exit 1
  fi
  KUBECONFIG="${LOCAL_KUBECONFIG}" "${REPOSITORY_ROOT}/hack/seed-local-secrets.sh"
}

status() {
  require_command kubectl
  require_command flux
  flux get all --all-namespaces --kubeconfig="${LOCAL_KUBECONFIG}"
  kubectl --kubeconfig "${LOCAL_KUBECONFIG}" get pods,pvc --all-namespaces
}

down() {
  require_command kind
  if ! cluster_exists; then
    printf 'Cluster %s does not exist.\n' "${CLUSTER_NAME}"
    return
  fi

  if kubectl --kubeconfig "${LOCAL_KUBECONFIG}" get configmap --all-namespaces \
    --selector='platform.galaxy-lab/signing-enabled=true' \
    --output=name 2>/dev/null | grep -q .; then
    printf 'Refusing to delete: desired state reports signing enabled. Stop the assignment and complete the slashing-history backup runbook first.\n' >&2
    exit 1
  fi

  kind delete cluster --name "${CLUSTER_NAME}"
  rm -f -- "${LOCAL_KUBECONFIG}"
}

usage() {
  printf 'Usage: %s {preflight|up|bootstrap|seed|status|down}\n' "$0" >&2
  exit 2
}

case "${1:-}" in
  preflight) preflight ;;
  up) up ;;
  bootstrap) bootstrap ;;
  seed) seed ;;
  status) status ;;
  down) down ;;
  *) usage ;;
esac
