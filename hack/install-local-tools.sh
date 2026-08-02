#!/usr/bin/env bash
set -euo pipefail

readonly KIND_VERSION="0.32.0"
readonly KIND_DARWIN_ARM64_SHA256="dca67911095a110c2b5c36e26df6cac860c602033e456c0db47be498cdef1ebb"
readonly FLUX_VERSION="2.8.8"
readonly FLUX_DARWIN_ARM64_SHA256="f54bb4d83cfc9563fd3213381d50fe1102215ef904ecf9c38afa183b02c74eb7"
readonly TERRAFORM_VERSION="1.15.8"
readonly TERRAFORM_DARWIN_ARM64_SHA256="f210110c5698b94d803a7a63cdb0251b5455c150841478808e2bbb343f95ed68"
readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly INSTALL_DIRECTORY="${REPOSITORY_ROOT}/.local/bin"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  printf 'The pinned local installer currently supports macOS arm64 only. Install kind %s, Flux %s, and Terraform %s manually on this platform.\n' \
    "${KIND_VERSION}" "${FLUX_VERSION}" "${TERRAFORM_VERSION}" >&2
  exit 1
fi

temporary_directory="$(mktemp -d)"
trap 'rm -rf "${temporary_directory}"' EXIT
mkdir -p "${INSTALL_DIRECTORY}"

curl --fail --silent --show-error --location \
  "https://kind.sigs.k8s.io/dl/v${KIND_VERSION}/kind-darwin-arm64" \
  --output "${temporary_directory}/kind"
printf '%s  %s\n' "${KIND_DARWIN_ARM64_SHA256}" "${temporary_directory}/kind" | shasum --algorithm 256 --check
install -m 0755 "${temporary_directory}/kind" "${INSTALL_DIRECTORY}/kind"

curl --fail --silent --show-error --location \
  "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_darwin_arm64.tar.gz" \
  --output "${temporary_directory}/flux.tar.gz"
printf '%s  %s\n' "${FLUX_DARWIN_ARM64_SHA256}" "${temporary_directory}/flux.tar.gz" | shasum --algorithm 256 --check
tar -xzf "${temporary_directory}/flux.tar.gz" -C "${temporary_directory}" flux
install -m 0755 "${temporary_directory}/flux" "${INSTALL_DIRECTORY}/flux"

curl --fail --silent --show-error --location \
  "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_darwin_arm64.zip" \
  --output "${temporary_directory}/terraform.zip"
printf '%s  %s\n' "${TERRAFORM_DARWIN_ARM64_SHA256}" "${temporary_directory}/terraform.zip" | shasum --algorithm 256 --check
unzip -q "${temporary_directory}/terraform.zip" terraform -d "${temporary_directory}"
install -m 0755 "${temporary_directory}/terraform" "${INSTALL_DIRECTORY}/terraform"

printf 'Installed project-local tools in %s\n' "${INSTALL_DIRECTORY}"
"${INSTALL_DIRECTORY}/kind" version
"${INSTALL_DIRECTORY}/flux" --version
"${INSTALL_DIRECTORY}/terraform" version
