#!/usr/bin/env bash
set -euo pipefail

# Guarded desired-capacity operations for the single EKS development lab.
# Terraform owns node-group shape and intentionally ignores desired_size after
# creation. This command owns only the bounded 0 <-> 1 lab transition.

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly AWS_BIN="${AWS_BIN:-aws}"
readonly KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"
readonly JQ_BIN="${JQ_BIN:-jq}"

cluster_name="${EKS_CLUSTER_NAME:-eth-validator-platform-dev}"
region="${AWS_REGION:-us-west-2}"
profile="${AWS_PROFILE:-default}"
kubeconfig="${EKS_KUBECONFIG:-${REPOSITORY_ROOT}/.local/eks-kubeconfig}"
availability_zone=""
timeout_seconds="${EKS_CAPACITY_TIMEOUT_SECONDS:-600}"
poll_seconds="${EKS_CAPACITY_POLL_SECONDS:-10}"
ethereum_groups_cache=""

usage() {
  cat >&2 <<EOF
Usage: $0 {status|pause|resume} [options]

Options:
  --az AZ               Required for pause and resume (for example us-west-2a)
  --cluster NAME        EKS cluster name (default: ${cluster_name})
  --region REGION       AWS region (default: ${region})
  --profile PROFILE     AWS CLI profile (default: ${profile})
  --kubeconfig PATH     Dedicated EKS kubeconfig (default: ${kubeconfig})

Environment-only test/timeout controls:
  EKS_CAPACITY_TIMEOUT_SECONDS, EKS_CAPACITY_POLL_SECONDS
EOF
  exit 2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "missing required command: $1"
  fi
}

aws_eks() {
  "${AWS_BIN}" eks "$@" --profile "${profile}" --region "${region}"
}

kubectl_eks() {
  "${KUBECTL_BIN}" --kubeconfig "${kubeconfig}" "$@"
}

parse_options() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --az)
        [[ $# -ge 2 ]] || usage
        availability_zone="$2"
        shift 2
        ;;
      --cluster)
        [[ $# -ge 2 ]] || usage
        cluster_name="$2"
        shift 2
        ;;
      --region)
        [[ $# -ge 2 ]] || usage
        region="$2"
        shift 2
        ;;
      --profile)
        [[ $# -ge 2 ]] || usage
        profile="$2"
        shift 2
        ;;
      --kubeconfig)
        [[ $# -ge 2 ]] || usage
        kubeconfig="$2"
        shift 2
        ;;
      *) usage ;;
    esac
  done
}

preflight() {
  require_command "${AWS_BIN}"
  require_command "${KUBECTL_BIN}"
  require_command "${JQ_BIN}"
  [[ -r "${kubeconfig}" ]] || fail "dedicated EKS kubeconfig is not readable: ${kubeconfig}"
  [[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || fail "timeout must be a positive integer"
  [[ "${poll_seconds}" =~ ^[0-9]+$ ]] || fail "poll interval must be a non-negative integer"
}

require_az() {
  [[ -n "${availability_zone}" ]] || fail "--az is required for this operation"
  [[ "${availability_zone}" =~ ^[a-z]{2}-[a-z]+-[0-9][a-z]$ ]] || fail "invalid Availability Zone: ${availability_zone}"
  [[ "${availability_zone}" == "${region}"? ]] || fail "Availability Zone ${availability_zone} is not in region ${region}"
}

verify_kubeconfig_cluster() {
  local expected_endpoint actual_endpoint
  expected_endpoint="$(aws_eks describe-cluster \
    --name "${cluster_name}" \
    --query 'cluster.endpoint' \
    --output text)"
  actual_endpoint="$(kubectl_eks config view --minify --output='jsonpath={.clusters[0].cluster.server}')"
  [[ "${actual_endpoint}" == "${expected_endpoint}" ]] || fail \
    "kubeconfig endpoint ${actual_endpoint} does not match EKS cluster ${cluster_name} (${expected_endpoint})"
}

ethereum_group_descriptions() {
  local groups group_names group description workload
  groups="$(aws_eks list-nodegroups --cluster-name "${cluster_name}" --output json)" || return 1
  group_names="$("${JQ_BIN}" -r '.nodegroups[]' <<<"${groups}")" || return 1
  while IFS= read -r group; do
    description="$(aws_eks describe-nodegroup \
      --cluster-name "${cluster_name}" \
      --nodegroup-name "${group}" \
      --output json)" || return 1
    workload="$("${JQ_BIN}" -r '.nodegroup.labels.workload // ""' <<<"${description}")" || return 1
    if [[ "${workload}" == "ethereum" ]]; then
      "${JQ_BIN}" -c '.' <<<"${description}" || return 1
    fi
  done <<<"${group_names}"
}

load_ethereum_groups() {
  ethereum_groups_cache="$(ethereum_group_descriptions)" ||
    fail "could not enumerate Ethereum node groups in ${cluster_name}"
  [[ -n "${ethereum_groups_cache}" ]] ||
    fail "no Ethereum node groups found in ${cluster_name}"
}

group_for_az() {
  local description group_az matches=0 selected=""
  while IFS= read -r description; do
    group_az="$("${JQ_BIN}" -r '.nodegroup.labels["platform.galaxy-lab/availability-zone"] // ""' <<<"${description}")"
    if [[ "${group_az}" == "${availability_zone}" ]]; then
      selected="${description}"
      matches=$((matches + 1))
    fi
  done <<<"${ethereum_groups_cache}"
  [[ "${matches}" -eq 1 ]] || fail \
    "expected exactly one Ethereum node group for ${availability_zone}; found ${matches}"
  printf '%s\n' "${selected}"
}

validate_group_contract() {
  local description="$1" status min_size max_size health_count group_az
  status="$("${JQ_BIN}" -r '.nodegroup.status' <<<"${description}")"
  min_size="$("${JQ_BIN}" -r '.nodegroup.scalingConfig.minSize' <<<"${description}")"
  max_size="$("${JQ_BIN}" -r '.nodegroup.scalingConfig.maxSize' <<<"${description}")"
  health_count="$("${JQ_BIN}" -r '.nodegroup.health.issues | length' <<<"${description}")"
  group_az="$("${JQ_BIN}" -r '.nodegroup.labels["platform.galaxy-lab/availability-zone"]' <<<"${description}")"

  [[ "${status}" == "ACTIVE" ]] || fail "node group in ${group_az} is ${status}, not ACTIVE"
  [[ "${health_count}" -eq 0 ]] || fail "node group in ${group_az} reports health issues"
  [[ "${min_size}" -eq 0 && "${max_size}" -eq 1 ]] || fail \
    "node group in ${group_az} is outside the lab's 0/1 capacity contract (${min_size}/${max_size})"
}

assert_single_active_group() {
  local allowed_group="$1" description name desired
  while IFS= read -r description; do
    name="$("${JQ_BIN}" -r '.nodegroup.nodegroupName' <<<"${description}")"
    desired="$("${JQ_BIN}" -r '.nodegroup.scalingConfig.desiredSize' <<<"${description}")"
    if [[ "${name}" != "${allowed_group}" && "${desired}" -ne 0 ]]; then
      fail "another Ethereum node group already has desired capacity: ${name}=${desired}"
    fi
  done <<<"${ethereum_groups_cache}"
}

assert_stopped_and_unsigned() {
  local records pods pvcs signing_count unsafe_lifecycle_count pod_count record_count pvc_count
  records="$(kubectl_eks get configmap --all-namespaces \
    --selector='app.kubernetes.io/part-of=ethereum-validator-platform' \
    --output=json)"
  pods="$(kubectl_eks get pods --all-namespaces \
    --selector='platform.galaxy-lab/component in (node,validator)' \
    --output=json)"
  pvcs="$(kubectl_eks get pvc --all-namespaces \
    --selector='app.kubernetes.io/part-of=ethereum-validator-platform' \
    --output=json)"

  signing_count="$("${JQ_BIN}" '[.items[] | select(
    .metadata.labels["platform.galaxy-lab/signing-enabled"] == "true" or
    .data.validatorEnabled == "true"
  )] | length' <<<"${records}")"
  unsafe_lifecycle_count="$("${JQ_BIN}" '[.items[] | select(
    .data.lifecycleState != null and
    .data.lifecycleState != "stopped" and
    .data.lifecycleState != "archived"
  )] | length' <<<"${records}")"
  record_count="$("${JQ_BIN}" '[.items[] | select(.data.lifecycleState != null)] | length' <<<"${records}")"
  pod_count="$("${JQ_BIN}" '.items | length' <<<"${pods}")"
  pvc_count="$("${JQ_BIN}" '.items | length' <<<"${pvcs}")"

  [[ "${signing_count}" -eq 0 ]] || fail "desired state still reports validator signing enabled"
  [[ "${unsafe_lifecycle_count}" -eq 0 ]] || fail "an assignment lifecycle is not stopped or archived"
  [[ "${pod_count}" -eq 0 ]] || fail "execution, consensus, or validator-client pods still exist"
  if [[ "${record_count}" -eq 0 && "${pvc_count}" -gt 0 ]]; then
    fail "platform PVCs exist without lifecycle records; refusing to infer stopped desired state"
  fi
}

assert_bound_pvcs_match_az() {
  local pvcs volume pv zones zone
  pvcs="$(kubectl_eks get pvc --all-namespaces \
    --selector='app.kubernetes.io/part-of=ethereum-validator-platform' \
    --output=json)"
  while IFS= read -r volume; do
    pv="$(kubectl_eks get pv "${volume}" --output=json)"
    zones="$("${JQ_BIN}" -r '[
      .spec.nodeAffinity.required.nodeSelectorTerms[]?.matchExpressions[]?
      | select(
          .operator == "In" and
          (
            .key == "topology.kubernetes.io/zone" or
            .key == "topology.ebs.csi.aws.com/zone"
          )
        )
      | .values[]
    ] | unique | .[]' <<<"${pv}")"
    [[ -n "${zones}" ]] || fail "bound PV ${volume} has no readable EBS Availability Zone"
    while IFS= read -r zone; do
      [[ "${zone}" == "${availability_zone}" ]] || fail \
        "bound PV ${volume} is in ${zone}; resume the ${zone} node group, not ${availability_zone}"
    done <<<"${zones}"
  done < <("${JQ_BIN}" -r '.items[] | select(.status.phase == "Bound") | .spec.volumeName' <<<"${pvcs}")
}

wait_for_update() {
  local group_name="$1" update_id="$2" started now status errors
  started="$(date +%s)"
  while true; do
    status="$(aws_eks describe-update \
      --name "${cluster_name}" \
      --nodegroup-name "${group_name}" \
      --update-id "${update_id}" \
      --query 'update.status' \
      --output text)"
    case "${status}" in
      Successful) return ;;
      Failed|Cancelled)
        errors="$(aws_eks describe-update \
          --name "${cluster_name}" \
          --nodegroup-name "${group_name}" \
          --update-id "${update_id}" \
          --query 'update.errors' \
          --output json)"
        fail "EKS update ${update_id} ended ${status}: ${errors}"
        ;;
    esac
    now="$(date +%s)"
    (( now - started < timeout_seconds )) || fail "timed out waiting for EKS update ${update_id}"
    sleep "${poll_seconds}"
  done
}

node_count_for_group() {
  local group_name="$1" nodes
  nodes="$(kubectl_eks get nodes \
    --selector="eks.amazonaws.com/nodegroup=${group_name}" \
    --output=json)"
  "${JQ_BIN}" -r '.items | length' <<<"${nodes}"
}

ready_node_count_for_group() {
  local group_name="$1" nodes
  nodes="$(kubectl_eks get nodes \
    --selector="eks.amazonaws.com/nodegroup=${group_name}" \
    --output=json)"
  "${JQ_BIN}" -r '[.items[] | select(
    any(.status.conditions[]; .type == "Ready" and .status == "True")
  )] | length' <<<"${nodes}"
}

wait_for_node_count() {
  local group_name="$1" expected="$2" started now count
  started="$(date +%s)"
  while true; do
    if [[ "${expected}" -eq 0 ]]; then
      count="$(node_count_for_group "${group_name}")"
    else
      count="$(ready_node_count_for_group "${group_name}")"
    fi
    [[ "${count}" -eq "${expected}" ]] && return
    now="$(date +%s)"
    (( now - started < timeout_seconds )) || fail \
      "timed out waiting for ${expected} Ready node(s) in ${group_name}; observed ${count}"
    sleep "${poll_seconds}"
  done
}

set_desired_size() {
  local group_name="$1" desired="$2" response update_id
  response="$(aws_eks update-nodegroup-config \
    --cluster-name "${cluster_name}" \
    --nodegroup-name "${group_name}" \
    --scaling-config "minSize=0,maxSize=1,desiredSize=${desired}" \
    --output json)"
  update_id="$("${JQ_BIN}" -r '.update.id' <<<"${response}")"
  [[ -n "${update_id}" && "${update_id}" != "null" ]] || fail "EKS returned no update ID"
  printf 'EKS update %s: %s desired %s\n' "${update_id}" "${group_name}" "${desired}"
  wait_for_update "${group_name}" "${update_id}"
}

status() {
  local description
  verify_kubeconfig_cluster
  load_ethereum_groups
  printf 'Cluster: %s (%s, profile %s)\n' "${cluster_name}" "${region}" "${profile}"
  printf '%-12s %-56s %-10s %-8s %-7s %-7s %-8s\n' AZ NODE_GROUP CAPACITY STATUS MIN MAX DESIRED
  while IFS= read -r description; do
    "${JQ_BIN}" -r '[
      .nodegroup.labels["platform.galaxy-lab/availability-zone"],
      .nodegroup.nodegroupName,
      .nodegroup.capacityType,
      .nodegroup.status,
      (.nodegroup.scalingConfig.minSize | tostring),
      (.nodegroup.scalingConfig.maxSize | tostring),
      (.nodegroup.scalingConfig.desiredSize | tostring)
    ] | @tsv' <<<"${description}" | while IFS=$'\t' read -r az name capacity state min max desired; do
      printf '%-12s %-56s %-10s %-8s %-7s %-7s %-8s\n' \
        "${az}" "${name}" "${capacity}" "${state}" "${min}" "${max}" "${desired}"
    done
  done <<<"${ethereum_groups_cache}"
  printf '\nEthereum workload nodes:\n'
  kubectl_eks get nodes --selector='workload=ethereum' --output=wide
  printf '\nAssignment lifecycle records and workload claims:\n'
  kubectl_eks get configmap,pvc --all-namespaces \
    --selector='app.kubernetes.io/part-of=ethereum-validator-platform' || true
}

pause() {
  local description group_name desired
  require_az
  verify_kubeconfig_cluster
  load_ethereum_groups
  description="$(group_for_az)"
  validate_group_contract "${description}"
  group_name="$("${JQ_BIN}" -r '.nodegroup.nodegroupName' <<<"${description}")"
  desired="$("${JQ_BIN}" -r '.nodegroup.scalingConfig.desiredSize' <<<"${description}")"
  assert_single_active_group "${group_name}"
  assert_stopped_and_unsigned
  if [[ "${desired}" -eq 0 ]]; then
    printf '%s is already paused at desired size 0.\n' "${group_name}"
    return
  fi
  [[ "${desired}" -eq 1 ]] || fail "expected desired size 0 or 1; found ${desired}"
  set_desired_size "${group_name}" 0
  wait_for_node_count "${group_name}" 0
  printf 'Paused %s. The zonal managed group remains available; no client or signing lifecycle was changed.\n' \
    "${group_name}"
}

resume() {
  local description group_name desired
  require_az
  verify_kubeconfig_cluster
  load_ethereum_groups
  description="$(group_for_az)"
  validate_group_contract "${description}"
  group_name="$("${JQ_BIN}" -r '.nodegroup.nodegroupName' <<<"${description}")"
  desired="$("${JQ_BIN}" -r '.nodegroup.scalingConfig.desiredSize' <<<"${description}")"
  assert_single_active_group "${group_name}"
  assert_stopped_and_unsigned
  assert_bound_pvcs_match_az
  if [[ "${desired}" -eq 1 ]]; then
    wait_for_node_count "${group_name}" 1
    printf '%s already has one Ready node. No client or signing lifecycle was changed.\n' "${group_name}"
    return
  fi
  [[ "${desired}" -eq 0 ]] || fail "expected desired size 0 or 1; found ${desired}"
  set_desired_size "${group_name}" 1
  wait_for_node_count "${group_name}" 1
  printf 'Resumed one Ready node in %s. Flux assignments remain stopped and signing remains disabled.\n' \
    "${availability_zone}"
}

command="${1:-}"
[[ -n "${command}" ]] || usage
shift
parse_options "$@"
preflight

case "${command}" in
  status) status ;;
  pause) pause ;;
  resume) resume ;;
  *) usage ;;
esac
