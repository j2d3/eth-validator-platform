#!/usr/bin/env bash
set -euo pipefail

# Post-restore signing recovery gate. This script never reads secret values and
# never changes GitOps desired state. It proves the conditions required before
# a reviewed activation change is reconciled.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-west-2}"
CLUSTER_NAME="${CLUSTER_NAME:-eth-validator-platform-dev}"
DB_IDENTIFIER="${DB_IDENTIFIER:-eth-validator-platform-dev-web3signer}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${ROOT_DIR}/.local/eks-kubeconfig}"
EXPECTED_SECRET_PREFIX="${EXPECTED_SECRET_PREFIX:-${CLUSTER_NAME}/}"

die() { printf 'signing-recovery: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

require_cluster() {
  local status
  status="$(aws eks describe-cluster --region "$AWS_REGION" --name "$CLUSTER_NAME" --query 'cluster.status' --output text)"
  [[ "$status" == "ACTIVE" ]] || die "EKS cluster is not ACTIVE: $status"
}

require_database() {
  local status
  status="$(aws rds describe-db-instances --region "$AWS_REGION" --db-instance-identifier "$DB_IDENTIFIER" --query 'DBInstances[0].DBInstanceStatus' --output text)"
  [[ "$status" == "available" ]] || die "RDS instance is not available: $status"
}

require_flux() {
  export KUBECONFIG="$KUBECONFIG_PATH"
  kubectl version --request-timeout=15s >/dev/null || die "cannot reach EKS through $KUBECONFIG_PATH"
  local required name ready suspended
  for required in infrastructure-controllers infrastructure-configs signer-infrastructure-configs signer-prerequisites apps; do
    read -r name ready suspended < <(kubectl -n flux-system get kustomization "$required" -o jsonpath='{.metadata.name} {.status.conditions[?(@.type=="Ready")].status} {.spec.suspend}{"\n"}')
    [[ "$ready" == "True" ]] || die "Flux Kustomization is not Ready: $required"
    [[ "$suspended" != "true" ]] || die "Flux Kustomization is suspended: $required"
  done
}

require_secret_inventory() {
  local count
  count="$(aws secretsmanager list-secrets --region "$AWS_REGION" --query 'SecretList[].Name' --output text | tr '\t' '\n' | awk -v prefix="$EXPECTED_SECRET_PREFIX" 'index($0,prefix)==1 {n++} END {print n+0}')"
  (( count >= 3 )) || die "expected durable secret containers are missing (found $count)"
  printf 'Durable secret containers discovered: %s (values not read)\n' "$count"
}

require_no_unapproved_signers() {
  export KUBECONFIG="$KUBECONFIG_PATH"
  local enabled
  enabled="$(kubectl get configmap -A -l 'platform.galaxy-lab/lifecycle' -o json | jq '[.items[] | select(.metadata.labels["platform.galaxy-lab/signing-enabled"] == "true")] | length')"
  [[ "$enabled" == "0" ]] || die "signing-enabled lifecycle records already exist: $enabled"
}

report() {
  export KUBECONFIG="$KUBECONFIG_PATH"
  printf 'EKS signing recovery gates passed for %s/%s\n' "$AWS_REGION" "$CLUSTER_NAME"
  kubectl -n flux-system get kustomizations infrastructure-controllers infrastructure-configs signer-infrastructure-configs signer-prerequisites apps \
    -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,SUSPENDED:.spec.suspend'
  kubectl -n signing get deployment -o name 2>/dev/null || true
  kubectl -n ethereum get pods -o name 2>/dev/null || true
  printf '%s\n' 'No signing authorization was changed. Apply activation only through a reviewed GitOps change.'
}

main() {
  local mode="${1:-observe}"
  [[ "$mode" == "observe" || "$mode" == "signing" ]] || die "usage: $0 [observe|signing]"
  need aws; need kubectl; need jq
  require_cluster
  require_database
  require_flux
  require_secret_inventory
  require_no_unapproved_signers
  if [[ "$mode" == "signing" ]]; then
    [[ "${RECOVERY_CONFIRMATION:-}" == "ephemery-testnet" ]] || die 'signing mode requires RECOVERY_CONFIRMATION=ephemery-testnet'
    printf '%s\n' 'Testnet signing gates are mechanically satisfied; a reviewed assignment activation is still required.'
  fi
  report
}

main "$@"
