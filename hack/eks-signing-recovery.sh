#!/usr/bin/env bash
set -euo pipefail

# Post-restore signing recovery gate. This script never reads secret values and
# never changes GitOps desired state. It proves the conditions required before
# a reviewed activation change is reconciled.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-west-2}"
CLUSTER_NAME="${CLUSTER_NAME:-eth-validator-platform-dev}"
DB_IDENTIFIER="${DB_IDENTIFIER:-eth-validator-platform-dev-web3signer}"
DB_IDENTIFIER_PREFIX="${DB_IDENTIFIER_PREFIX:-${CLUSTER_NAME}-web3signer}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${ROOT_DIR}/.local/eks-kubeconfig}"
EXPECTED_SECRET_PREFIX="${EXPECTED_SECRET_PREFIX:-${CLUSTER_NAME}/}"
EXPECTED_SECRET_NAMES="${EXPECTED_SECRET_NAMES:-${CLUSTER_NAME}/ethereum/engine-jwt,${CLUSTER_NAME}/signing/web3signer-database,${CLUSTER_NAME}/signing/validator-keystore,${CLUSTER_NAME}/signing/validator-keystore-02,${CLUSTER_NAME}/signing/validator-keystore-03,${CLUSTER_NAME}/signing/validator-keystore-04,${CLUSTER_NAME}/signing/validator-keystore-05}"

die() { printf 'signing-recovery: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

require_cluster() {
  local status
  status="$(aws eks describe-cluster --region "$AWS_REGION" --name "$CLUSTER_NAME" --query 'cluster.status' --output text)"
  [[ "$status" == "ACTIVE" ]] || die "EKS cluster is not ACTIVE: $status"
}

require_database() {
  local status identifier
  local -a identifiers matching_identifiers
  status="$(aws rds describe-db-instances --region "$AWS_REGION" --db-instance-identifier "$DB_IDENTIFIER" --query 'DBInstances[0].DBInstanceStatus' --output text)"
  [[ "$status" == "available" ]] || die "RDS instance is not available: $status"

  while IFS= read -r identifier; do
    [[ -n "$identifier" ]] && identifiers+=("$identifier")
  done < <(aws rds describe-db-instances --region "$AWS_REGION" --query 'DBInstances[].DBInstanceIdentifier' --output text | tr '\t' '\n')
  for identifier in "${identifiers[@]}"; do
    [[ "$identifier" == "$DB_IDENTIFIER_PREFIX"* ]] && matching_identifiers+=("$identifier")
  done
  (( ${#matching_identifiers[@]} == 1 )) || die "expected exactly one signer-tier RDS instance with prefix $DB_IDENTIFIER_PREFIX (found ${#matching_identifiers[@]})"
  [[ "${matching_identifiers[0]}" == "$DB_IDENTIFIER" ]] || die "signer-tier RDS instance does not match DB_IDENTIFIER: ${matching_identifiers[0]}"
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
  local secret expected found
  local -a discovered expected_names actual_names
  while IFS= read -r secret; do
    [[ -n "$secret" ]] && discovered+=("$secret")
  done < <(aws secretsmanager list-secrets --region "$AWS_REGION" --query 'SecretList[].Name' --output text | tr '\t' '\n')
  IFS=',' read -r -a expected_names <<< "$EXPECTED_SECRET_NAMES"
  (( ${#expected_names[@]} > 0 )) || die 'EXPECTED_SECRET_NAMES must not be empty'

  for expected in "${expected_names[@]}"; do
    [[ "$expected" == "$EXPECTED_SECRET_PREFIX"* ]] || die "expected secret is outside EXPECTED_SECRET_PREFIX: $expected"
  done
  for secret in "${discovered[@]}"; do
    [[ "$secret" == "$EXPECTED_SECRET_PREFIX"* ]] && actual_names+=("$secret")
  done
  (( ${#actual_names[@]} == ${#expected_names[@]} )) || die "durable secret inventory does not match expected count (found ${#actual_names[@]}, expected ${#expected_names[@]})"
  for expected in "${expected_names[@]}"; do
    found=false
    for secret in "${actual_names[@]}"; do
      [[ "$secret" == "$expected" ]] && found=true && break
    done
    "$found" || die "expected durable secret container is missing: $expected"
  done
  printf 'Durable secret inventory matches %s expected containers (values not read)\n' "${#expected_names[@]}"
}

require_no_unapproved_signers() {
  export KUBECONFIG="$KUBECONFIG_PATH"
  local enabled
  # This intentionally targets lifecycle records only. platform-profile is an
  # environment profile, not a workload lifecycle record, even when it carries
  # the signing-enabled label for configuration.
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
