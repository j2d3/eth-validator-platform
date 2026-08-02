SHELL := /bin/bash
.DEFAULT_GOAL := help
LOCAL_BIN := $(CURDIR)/.local/bin
TF_PLUGIN_CACHE_DIR := $(CURDIR)/.local/terraform-plugin-cache
export PATH := $(LOCAL_BIN):$(PATH)

.PHONY: help tools format fmt validate catalog test container-contracts helm-template helm-releases kustomize-build verify-scripts local-preflight local-up local-bootstrap local-seed local-status local-down check

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

tools: ## Install pinned kind, Flux, and Terraform binaries under .local/bin
	./hack/install-local-tools.sh

format: ## Format Terraform files in place
	terraform fmt -recursive

fmt: ## Check Terraform formatting without modifying files
	terraform fmt -check -recursive

validate: ## Initialize without a backend and validate each Terraform root
	@mkdir -p "$(TF_PLUGIN_CACHE_DIR)"
	@for root in terraform/bootstrap terraform/environments/dev; do \
		if [[ -d "$$root" ]]; then \
			TF_PLUGIN_CACHE_DIR="$(TF_PLUGIN_CACHE_DIR)" terraform -chdir="$$root" init -backend=false -input=false; \
			TF_PLUGIN_CACHE_DIR="$(TF_PLUGIN_CACHE_DIR)" terraform -chdir="$$root" validate; \
		fi; \
	done

catalog: ## Validate desired-state schemas, relations, and generated local projection
	python3 tools/validate_catalog.py
	python3 tools/render_local_assignments.py --check

test: ## Run desired-state safety unit tests
	python3 -m unittest discover -s tests -v

container-contracts: ## Verify pinned images match declared Kubernetes runtime identities
	python3 tools/verify_container_contracts.py

helm-template: ## Render and lint the selectable Ethereum client chart
	./hack/test-ethereum-chart.sh

helm-releases: ## Render pinned third-party Helm releases with declared values
	./hack/validate-helm-releases.sh

kustomize-build: ## Build the Flux entrypoint without contacting a cluster
	kubectl kustomize platform/infrastructure/controllers > /dev/null
	kubectl kustomize platform/infrastructure/configs/local > /dev/null
	kubectl kustomize platform/apps/prerequisites/local > /dev/null
	kubectl kustomize platform/apps/local > /dev/null

verify-scripts: ## Syntax-check bash scripts under hack/
	@for script in hack/*.sh; do bash -n "$$script" || exit 1; done
	@echo "Syntax-checked hack/ scripts."

local-preflight: ## Verify local Kubernetes and GitOps prerequisites
	./hack/local-cluster.sh preflight

local-up: ## Create the digest-pinned local kind cluster
	./hack/local-cluster.sh up

local-bootstrap: ## Bootstrap Flux against the private GitHub repository
	./hack/local-cluster.sh bootstrap

local-seed: ## Seed local source Secrets from Git-ignored files
	./hack/local-cluster.sh seed

local-status: ## Show Flux and workload reconciliation status
	./hack/local-cluster.sh status

local-down: ## Delete the local cluster after the signing-safety guard passes
	./hack/local-cluster.sh down

check: fmt catalog test helm-template kustomize-build verify-scripts ## Run offline local validation
