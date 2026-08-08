#!/usr/bin/env bash
set -euo pipefail

# Guarded cold-standby operator for the single development EKS environment.
# This is intentionally a trusted-local tool. It does not run in GitHub
# Actions, and it never reads or prints secret values.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_ROOT="${ROOT_DIR}/terraform/environments/dev"
TF_BIN="${TF_BIN:-${ROOT_DIR}/.local/bin/terraform}"
AWS_REGION="${AWS_REGION:-us-west-2}"
CLUSTER_NAME="${CLUSTER_NAME:-eth-validator-platform-dev}"
DB_IDENTIFIER="${DB_IDENTIFIER:-eth-validator-platform-dev-web3signer}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${ROOT_DIR}/.local/eks-kubeconfig}"
STATE_DIR="${ROOT_DIR}/.local/cold-standby"
CONFIRMATION="destroy-${CLUSTER_NAME}"

die() {
  printf 'cold-standby: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_tools() {
  require_command aws
  require_command jq
  require_command kubectl
  test -x "$TF_BIN" || die "project-local Terraform not found at $TF_BIN"
  mkdir -p "$STATE_DIR"
}

tf() {
  "$TF_BIN" -chdir="$TF_ROOT" "$@"
}

backend_bucket() {
  awk -F'"' '/^bucket[[:space:]]*=/{print $2; exit}' \
    "$TF_ROOT/backend.hcl"
}

snapshot_id_from_arg_or_file() {
  if [[ -n "${SNAPSHOT_ID:-}" ]]; then
    printf '%s\n' "$SNAPSHOT_ID"
  elif [[ -f "$STATE_DIR/snapshot-id" ]]; then
    cat "$STATE_DIR/snapshot-id"
  else
    die "set SNAPSHOT_ID or run snapshot first"
  fi
}

verify_snapshot() {
  local snapshot_id="$1"
  local status encrypted source
  read -r status encrypted source < <(
    aws rds describe-db-snapshots \
      --region "$AWS_REGION" \
      --db-snapshot-identifier "$snapshot_id" \
      --query 'DBSnapshots[0].[Status,Encrypted,DBInstanceIdentifier]' \
      --output text
  )
  [[ "$status" == "available" ]] || die "RDS snapshot is not available: $snapshot_id ($status)"
  [[ "$encrypted" == "True" ]] || die "RDS snapshot is not encrypted: $snapshot_id"
  [[ "$source" == "$DB_IDENTIFIER" ]] || die "snapshot source is $source, expected $DB_IDENTIFIER"
}

snapshot() {
  require_tools
  local snapshot_id="${SNAPSHOT_ID:-${DB_IDENTIFIER}-cold-standby-$(date -u +%Y%m%d-%H%M%S)}"
  if aws rds describe-db-snapshots --region "$AWS_REGION" \
    --db-snapshot-identifier "$snapshot_id" >/dev/null 2>&1; then
    printf 'Using existing snapshot %s\n' "$snapshot_id"
  else
    printf 'Creating encrypted snapshot %s\n' "$snapshot_id"
    aws rds create-db-snapshot \
      --region "$AWS_REGION" \
      --db-instance-identifier "$DB_IDENTIFIER" \
      --db-snapshot-identifier "$snapshot_id" \
      --tags Key=Project,Value=eth-validator-platform \
             Key=Purpose,Value=cold-standby \
             Key=DataClassification,Value=slashing-protection >/dev/null
  fi
  aws rds wait db-snapshot-available \
    --region "$AWS_REGION" \
    --db-snapshot-identifier "$snapshot_id"
  verify_snapshot "$snapshot_id"

  local bucket manifest
  bucket="$(backend_bucket)"
  manifest="$STATE_DIR/${snapshot_id}.json"
  aws rds describe-db-snapshots \
    --region "$AWS_REGION" \
    --db-snapshot-identifier "$snapshot_id" \
    --query 'DBSnapshots[0]' --output json >"$STATE_DIR/snapshot.json"
  jq -n \
    --arg snapshot "$snapshot_id" \
    --arg git "$(git -C "$ROOT_DIR" rev-parse HEAD)" \
    --arg created "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg region "$AWS_REGION" \
    --slurpfile rds "$STATE_DIR/snapshot.json" \
    '{schemaVersion:1,environment:"dev",region:$region,createdAt:$created,gitRevision:$git,rds:{snapshotIdentifier:$snapshot,encrypted:($rds[0].Encrypted),status:($rds[0].Status),allocatedStorageGiB:($rds[0].AllocatedStorage),source:($rds[0].DBInstanceIdentifier)}}' \
    >"$manifest"
  chmod 600 "$manifest"
  aws s3 cp "$manifest" \
    "s3://${bucket}/cold-standby/manifests/${snapshot_id}.json" \
    --sse AES256 >/dev/null
  printf '%s\n' "$snapshot_id" >"$STATE_DIR/snapshot-id"
  printf 'Verified encrypted recovery snapshot: %s\n' "$snapshot_id"
}

preflight() {
  require_tools
  local db_status deletion_protection
  read -r db_status deletion_protection < <(
    aws rds describe-db-instances \
      --region "$AWS_REGION" \
      --db-instance-identifier "$DB_IDENTIFIER" \
      --query 'DBInstances[0].[DBInstanceStatus,DeletionProtection]' \
      --output text
  )
  [[ "$db_status" == "available" ]] || die "RDS is not available: $db_status"
  [[ "$deletion_protection" == "True" ]] || die "RDS deletion protection is not enabled"

  export KUBECONFIG="$KUBECONFIG_PATH"
  kubectl version --request-timeout=10s >/dev/null || die "cannot reach EKS through $KUBECONFIG_PATH"

  local signing_records running_workloads
  signing_records="$(kubectl get configmap -A -l 'platform.galaxy-lab/lifecycle' -o json \
    | jq '[.items[] | select(.metadata.labels["platform.galaxy-lab/signing-enabled"] == "true")] | length')"
  [[ "$signing_records" == "0" ]] || die "signing-enabled lifecycle records remain: $signing_records"

  running_workloads="$(kubectl -n ethereum get pods -o json \
    | jq '[.items[] | select(.status.phase != "Succeeded" and .status.phase != "Failed")] | length')"
  [[ "$running_workloads" == "0" ]] || die "Ethereum workload Pods remain: $running_workloads"

  printf 'Preflight passed: signing disabled, no Ethereum workload Pods, RDS protected.\n'
}

destroy_plan() {
  require_tools
  local snapshot_id final_id plan_path
  snapshot_id="$(snapshot_id_from_arg_or_file)"
  verify_snapshot "$snapshot_id"
  final_id="${DB_IDENTIFIER}-cold-final-$(date -u +%Y%m%d-%H%M%S)"
  plan_path="$STATE_DIR/destroy-${final_id}.tfplan"
  tf plan -destroy -refresh=false -input=false \
    -var='rds_deletion_protection=false' \
    -var="rds_final_snapshot_identifier=${final_id}" \
    -out="$plan_path" -no-color >"$STATE_DIR/destroy-${final_id}.txt"
  local secret_deletes
  secret_deletes="$(tf show -json "$plan_path" | jq '[.resource_changes[] | select((.change.actions | index("delete")) and (.address | test("secretsmanager_secret")))] | length')"
  [[ "$secret_deletes" == "0" ]] || die "destroy plan contains $secret_deletes Secrets Manager deletions"
  printf 'Destroy plan is safe for durable secrets: %s\n' "$plan_path"
  rg '^Plan:' "$STATE_DIR/destroy-${final_id}.txt" || true
}

down() {
  require_tools
  preflight
  local snapshot_id final_id plan_path started confirmation
  snapshot_id="$(snapshot_id_from_arg_or_file)"
  verify_snapshot "$snapshot_id"
  final_id="${DB_IDENTIFIER}-cold-final-$(date -u +%Y%m%d-%H%M%S)"
  plan_path="$STATE_DIR/destroy-${final_id}.tfplan"
  started="$(date +%s)"
  tf plan -destroy -refresh=false -input=false \
    -var='rds_deletion_protection=false' \
    -var="rds_final_snapshot_identifier=${final_id}" \
    -out="$plan_path" -no-color >"$STATE_DIR/destroy-${final_id}.txt"
  local secret_deletes
  secret_deletes="$(tf show -json "$plan_path" | jq '[.resource_changes[] | select((.change.actions | index("delete")) and (.address | test("secretsmanager_secret")))] | length')"
  [[ "$secret_deletes" == "0" ]] || die "destroy plan contains durable secret deletions"
  rg '^Plan:' "$STATE_DIR/destroy-${final_id}.txt" || true
  printf 'Type %s to authorize the cold teardown: ' "$CONFIRMATION"
  read -r confirmation
  [[ "$confirmation" == "$CONFIRMATION" ]] || die "confirmation did not match; no AWS mutation performed"
  tf apply -input=false -auto-approve "$plan_path"
  printf 'Cold teardown elapsed seconds: %s\n' "$(( $(date +%s) - started ))"
}

up() {
  require_tools
  local snapshot_id started
  snapshot_id="$(snapshot_id_from_arg_or_file)"
  verify_snapshot "$snapshot_id"
  started="$(date +%s)"
  tf apply -input=false -auto-approve \
    -var="rds_snapshot_identifier=${snapshot_id}"
  aws eks update-kubeconfig --region "$AWS_REGION" --name "$CLUSTER_NAME" --alias "$CLUSTER_NAME" >/dev/null
  printf 'Terraform/EKS restore elapsed seconds: %s\n' "$(( $(date +%s) - started ))"
  printf '%s\n' 'Flux bootstrap remains an explicit post-restore step; follow docs/runbooks/eks-flux-bootstrap.md.'
}

usage() {
  printf 'Usage: %s {preflight|snapshot|destroy-plan|down|up}\n' "$0" >&2
  exit 2
}

case "${1:-}" in
  preflight) preflight ;;
  snapshot) snapshot ;;
  destroy-plan) destroy_plan ;;
  down) down ;;
  up) up ;;
  *) usage ;;
esac
