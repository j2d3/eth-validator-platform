#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "${temporary_directory}"' EXIT

extract() {
  local manifest="$1"
  local expression="$2"
  ruby -ryaml -e 'document = YAML.load_file(ARGV[0]); value = ARGV[1].split(".").reduce(document) { |memo, key| memo.fetch(key) }; puts value.is_a?(Hash) ? YAML.dump(value) : value' \
    "${manifest}" "${expression}"
}

render() {
  local manifest="$1"
  local repository_url="$2"
  local release_name
  local namespace
  local chart
  local version
  local values_file

  release_name="$(extract "${manifest}" metadata.name)"
  namespace="$(extract "${manifest}" metadata.namespace)"
  chart="$(extract "${manifest}" spec.chart.spec.chart)"
  version="$(extract "${manifest}" spec.chart.spec.version)"
  values_file="${temporary_directory}/${release_name}-values.yaml"
  extract "${manifest}" spec.values >"${values_file}"

  printf 'Rendering %s %s\n' "${chart}" "${version}"
  helm template "${release_name}" "${chart}" \
    --repo "${repository_url}" \
    --version "${version}" \
    --namespace "${namespace}" \
    --include-crds \
    --values "${values_file}" >/dev/null
}

render \
  "${REPOSITORY_ROOT}/platform/infrastructure/controllers/external-secrets.yaml" \
  "https://charts.external-secrets.io"
render \
  "${REPOSITORY_ROOT}/platform/infrastructure/controllers/cloudnative-pg.yaml" \
  "https://cloudnative-pg.github.io/charts"
render \
  "${REPOSITORY_ROOT}/platform/infrastructure/controllers/monitoring.yaml" \
  "https://prometheus-community.github.io/helm-charts"
